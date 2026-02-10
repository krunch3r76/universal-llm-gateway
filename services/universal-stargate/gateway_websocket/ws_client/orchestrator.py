"""Gateway WebSocket client orchestrator.

High-level orchestration of WebSocket connection, state management,
event publishing, and message handling.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from ..messages import ResourcesData
from .connection import ConnectionManager, ConnectionState
from .events import EventPublisher
from .queries import QueryManager
from .registry_wiring import build_handler_context, create_handler_registry
from .state import GatewayState

logger = get_logger(__name__)


class GatewayWebSocketClient:
    """
    WebSocket client for Gateway control plane.

    This client:
    - Maintains persistent WebSocket connection to Gateway
    - Caches all startup data from INIT message
    - Handles real-time updates (MODEL_LOADED, etc.)
    - Provides instant access to cached state (no HTTP round-trip)
    - Emits GATEWAY_STATE_CHANGED events on connection transitions

    State machine:
    - DISCONNECTED: No connection
    - CONNECTING: Initial connection attempt
    - CONNECTED: Active connection, receiving events
    - RECONNECTING: Connection lost, attempting to reconnect

    Health model:
    - Connected = Healthy (WebSocket open)
    - Disconnected = Unhealthy (no fallback to HTTP)

    Event-Driven Architecture:
    - Connection callbacks emit GATEWAY_STATE_CHANGED events directly
    - No polling required - consumers react to events
    """

    def __init__(
        self,
        gateway_url: str,
        gateway_name: str = "gateway",
        reconnect_interval: float = 5.0,
        max_reconnect_attempts: int = 0,  # 0 = infinite
        connect_timeout: float = 10.0,
        event_bus: Any = None,
        socket_path: str | None = None,
        on_after_init: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        # Diagnostic logging
        logger.info(
            f"GatewayWebSocketClient.__init__(): gateway_name={gateway_name}, "
            f"socket_path={socket_path}, gateway_url={gateway_url}"
        )

        # CRITICAL: Validate socket_path consistency
        # If gateway_url is just "localhost" (no port), socket_path should be set
        if gateway_url and "localhost" in gateway_url and ":999" not in gateway_url:
            if not socket_path:
                logger.error(
                    f"CRITICAL: gateway_url={gateway_url} suggests Unix socket, "
                    f"but socket_path is None. This will cause TCP fallback."
                )

        # Convert HTTP URL to WebSocket URL (for path extraction)
        ws_url = gateway_url.replace("http://", "ws://").replace("https://", "wss://")
        self._ws_url = f"{ws_url.rstrip('/')}/ws/stargate"
        self._gateway_name = gateway_name
        self._socket_path = socket_path  # NEW: Store for reference

        # Components - pass socket_path to ConnectionManager
        self._connection = ConnectionManager(
            self._ws_url,
            gateway_name,
            reconnect_interval,
            max_reconnect_attempts,
            connect_timeout,
            socket_path=socket_path,  # NEW: Pass socket_path
        )
        self._state = GatewayState()
        self._event_publisher = EventPublisher(self._ws_url, gateway_name, event_bus)
        self._query_manager = QueryManager()
        self._event_bus = event_bus
        self._on_after_init = on_after_init

        # Handler registry (replaces if/elif dispatch)
        self._handler_registry = create_handler_registry()

        # Event callbacks (global - called for ALL models)
        self._on_connected: Callable[[], Awaitable[None]] | None = None
        self._on_disconnected: Callable[[], Awaitable[None]] | None = None
        self._on_model_loading_started: Callable[[str], Awaitable[None]] | None = None
        self._on_model_loaded: Callable[[str, dict], Awaitable[None]] | None = None
        self._on_model_unloaded: Callable[[str], Awaitable[None]] | None = None
        self._on_model_load_failed: Callable[[str, str], Awaitable[None]] | None = None
        self._on_model_busy: Callable[[str], Awaitable[None]] | None = None
        self._on_model_idle: Callable[[str, dict], Awaitable[None]] | None = None
        self._on_resource_update: Callable[[dict], Awaitable[None]] | None = None
        self._on_catalog_update: Callable[[dict], Awaitable[None]] | None = None
        self._on_telemetry_heartbeat: Callable[[dict], Awaitable[None]] | None = None

        # Model-specific callbacks (keyed by routing_key, multiple per key)
        # Used by LoadOutcomeTracker for concurrent load tracking
        # Stores sets to support multiple trackers for same model
        self._model_loaded_callbacks: dict[
            str, set[Callable[[str, dict], Awaitable[None]]]
        ] = {}
        self._model_load_failed_callbacks: dict[
            str, set[Callable[[str, str], Awaitable[None]]]
        ] = {}

        # Subscribe to resource reservation events
        self._subscribe_to_reservation_events()

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def is_connected(self) -> bool:
        """True if WebSocket is connected and INIT received."""
        return self._connection.is_connected

    @property
    def is_healthy(self) -> bool:
        """Gateway is healthy iff WebSocket is connected."""
        return self.is_connected

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._connection.state

    @property
    def gateway_name(self) -> str:
        """Gateway name from INIT message."""
        return self._state.gateway_name

    @property
    def gateway_version(self) -> str:
        """Gateway version from INIT message."""
        return self._state.gateway_version

    # =========================================================================
    # State Access (Instant, No HTTP)
    # =========================================================================

    def get_models(self) -> set[str]:
        """Get available model IDs (instant, from cache)."""
        return self._state.get_models()

    def get_resources(self) -> ResourcesData:
        """Get resource status (instant, from cache)."""
        return self._state.get_resources()

    def get_catalog(self) -> dict[str, Any]:
        """Get catalog data (instant, from cache)."""
        return self._state.get_catalog()

    def get_activated_contexts(self) -> dict[str, dict]:
        """Get activated contexts from catalog (instant, from cache)."""
        return self._state.get_activated_contexts()

    def get_transformations(self) -> dict[str, Any]:
        """Get catalog transformations (instant, from cache)."""
        return self._state.get_transformations()

    def get_resource_status(self) -> ResourcesData | None:
        """
        Get current resource status from real-time WebSocket state.

        Returns:
            ResourcesData with metrics and model state, or None if disconnected.

        Event-driven: automatically updated on RESOURCE_UPDATE/MODEL_LOADED/
        UNLOADED events.
        This is the ONLY source of resource status - no HTTP fallback.
        """
        return self._state.get_resource_status(self.is_connected)

    def get_loaded_models(self) -> frozenset[str]:
        """
        Get current set of loaded models from real-time WebSocket state.

        Event-driven: automatically updated on MODEL_LOADED/MODEL_UNLOADED events.
        """
        return self._state.get_loaded_models()

    def get_busy_models(self) -> frozenset[str]:
        """
        Get current set of busy models from real-time WebSocket state.

        Event-driven: automatically updated on MODEL_BUSY/MODEL_IDLE events.
        """
        return self._state.get_busy_models()

    def get_loading_models(self) -> frozenset[str]:
        """
        Get current set of models currently loading from real-time WebSocket state.

        Event-driven: automatically updated on MODEL_LOADING_STARTED/
        MODEL_LOADED events.
        """
        return self._state.get_loading_models()

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def connect(self) -> bool:
        """
        Connect to Gateway WebSocket.

        Returns:
            True if connected and INIT received, False otherwise.

        Note: Message loop and event emission are handled by
        _on_connection_established callback (works for both initial
        connect and reconnection).
        """
        success = await self._connection.connect(
            on_init=self._on_init_received, on_connected=self._on_connection_established
        )

        if not success:
            # Start reconnect loop for failed initial connection
            self._connection.start_reconnect_loop(
                on_init=self._on_init_received,
                on_connected=self._on_connection_established,
            )

        return success

    async def disconnect(self) -> None:
        """Disconnect from Gateway WebSocket."""
        await self._connection.disconnect()

    async def wait_ready(self, timeout: float | None = None) -> bool:
        """
        Wait for connection to be ready (INIT received).

        Args:
            timeout: Maximum seconds to wait (None = wait forever)

        Returns:
            True if ready, False if timeout
        """
        return await self._connection.wait_ready(timeout)

    # =========================================================================
    # Message Handling
    # =========================================================================

    def _on_init_received(self, data: dict[str, Any]) -> None:
        """
        Callback when INIT message received.

        Args:
            data: INIT message data
        """
        self._state.process_init(data)
        if self._on_after_init:
            self._on_after_init(self._gateway_name, data)

    async def _on_connection_established(self) -> None:
        """
        Callback when connection is established (after INIT).

        CRITICAL: This is called after BOTH initial connect AND reconnection.
        Must start message loop here to ensure it runs after reconnection.
        """
        logger.info(f"🔍 {self._gateway_name}: _on_connection_established() STARTING")

        # Emit connected event (event-driven architecture)
        logger.info(
            f"🔍 {self._gateway_name}: Emitting gateway_state_changed (connected=True)"
        )
        await self._event_publisher.emit_gateway_state_changed(connected=True)

        logger.info(
            f"✅ Connected to Gateway '{self.gateway_name}' "
            f"v{self.gateway_version}: {len(self._state.get_models())} models, "
            f"{len(self._state.get_loaded_models())} loaded"
        )

        # Start message handling loop
        # CRITICAL: Must be started here to work after reconnection
        logger.info(f"🔍 {self._gateway_name}: About to call start_message_loop()...")
        self._connection.start_message_loop(
            on_message=self._handle_message,
            on_disconnected=self._on_connection_lost,
        )
        logger.info(f"🔍 {self._gateway_name}: start_message_loop() returned")

        # Notify user callback (fire-and-forget)
        if self._on_connected:
            logger.info(f"🔍 {self._gateway_name}: Notifying user callback")
            asyncio.create_task(self._safe_callback(self._on_connected))

        logger.info(f"🔍 {self._gateway_name}: _on_connection_established() COMPLETE")

    async def _on_connection_lost(self) -> None:
        """Callback when connection is lost."""
        # Emit disconnected event (event-driven architecture)
        await self._event_publisher.emit_gateway_state_changed(connected=False)

        # Notify user callback (fire-and-forget)
        if self._on_disconnected:
            asyncio.create_task(self._safe_callback(self._on_disconnected))

        # Start reconnection
        self._connection.start_reconnect_loop(
            on_init=self._on_init_received,
            on_connected=self._on_connection_established,
        )

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """
        Dispatch WebSocket message to appropriate handler.

        Uses handler registry for O(1) dispatch.
        Non-blocking: sync handlers inline, async handlers minimal I/O only.

        Args:
            message: Parsed WebSocket message
        """
        msg_type = message.get("type")
        data = message.get("data", {})

        # Validate data is dict
        if not isinstance(data, dict):
            logger.warning(f"Message data is not dict: {type(data)}")
            data = {}

        ctx = self._build_handler_context()
        await self._handler_registry.dispatch(msg_type, data, ctx)

    def _build_handler_context(self):
        """Build context for message handlers."""
        return build_handler_context(
            state=self._state,
            event_publisher=self._event_publisher,
            query_manager=self._query_manager,
            ws_url=self._ws_url,
            gateway_name=self._gateway_name,
            send_message=self._send_message,
            schedule_callback=self._schedule_callback,
            on_model_loading_started=self._on_model_loading_started,
            on_model_loaded=self._on_model_loaded,
            on_model_unloaded=self._on_model_unloaded,
            on_model_load_failed=self._on_model_load_failed,
            on_model_busy=self._on_model_busy,
            on_model_idle=self._on_model_idle,
            on_resource_update=self._on_resource_update,
            on_catalog_update=self._on_catalog_update,
            on_telemetry_heartbeat=self._on_telemetry_heartbeat,
            # Model-specific callbacks for concurrent load tracking
            model_loaded_callbacks=self._model_loaded_callbacks,
            model_load_failed_callbacks=self._model_load_failed_callbacks,
        )

    def _schedule_callback(self, callback: Callable, args: tuple) -> None:
        """Schedule callback execution (fire-and-forget)."""
        logger.debug(
            f"📞 GatewayWebSocketClient: Scheduling callback {callback} "
            f"with args {args}"
        )
        asyncio.create_task(self._safe_callback(callback, *args))

    async def _send_message(self, message: str) -> None:
        """Send message to WebSocket."""
        ws = self._connection.ws
        if ws:
            await ws.send(message)

    async def _safe_callback(self, callback: Callable, *args) -> None:
        """Execute callback safely with error logging."""
        logger.debug(
            f"🔧 GatewayWebSocketClient: Executing callback {callback} with args {args}"
        )
        try:
            await callback(*args)
            logger.debug(
                f"✅ GatewayWebSocketClient: Callback {callback} executed successfully"
            )
        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)

    def _subscribe_to_reservation_events(self) -> None:
        """Subscribe to RESOURCE_RESERVED and RESOURCE_RELEASED events."""
        if not self._event_bus:
            return

        try:
            from src.scheduling.events import RESOURCE_RELEASED, RESOURCE_RESERVED

            # Subscribe to reservation events for this gateway
            self._event_bus.subscribe_async(
                RESOURCE_RESERVED, self._on_resource_reserved
            )
            self._event_bus.subscribe_async(
                RESOURCE_RELEASED, self._on_resource_released
            )

            logger.debug(
                f"✅ Subscribed to resource reservation events for {self._gateway_name}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to subscribe to reservation events for "
                f"{self._gateway_name}: {e}"
            )

    def _parse_reservation_event(
        self, event: Any
    ) -> tuple[str, str | None, int, int, str]:
        """
        Parse reservation event payload.

        Returns:
            (gateway_name, model_id, vram_mb, ram_mb, reason)
        """
        payload = event.payload if hasattr(event, "payload") else event
        return (
            payload.get("gateway_name", ""),
            payload.get("model_id"),
            payload.get("vram_mb", 0),
            payload.get("ram_mb", 0),
            payload.get("reason", "unknown"),
        )

    def _is_for_this_gateway(self, gateway_name: str) -> bool:
        """Check if event targets this gateway."""
        return gateway_name == self._gateway_name

    def _log_reservation_event(
        self,
        event_type: str,
        model_id: str | None,
        vram_mb: int,
        ram_mb: int,
        reason: str | None = None,
    ) -> None:
        """Log reservation event with current effective resources."""
        reason_part = f" reason={reason}" if reason else ""
        logger.debug(
            f"{event_type} gateway={self._gateway_name} "
            f"model={model_id} vram={vram_mb}MB ram={ram_mb}MB{reason_part} "
            f"effective_vram={self._state.resources.available_vram_mb}MB "
            f"effective_ram={self._state.resources.available_ram_mb}MB"
        )

    async def _on_resource_reserved(self, event: Any) -> None:
        """Handle RESOURCE_RESERVED event."""
        try:
            gateway_name, model_id, vram_mb, ram_mb, _ = self._parse_reservation_event(
                event
            )
            if not self._is_for_this_gateway(gateway_name):
                return

            self._state.apply_reservation(vram_mb=vram_mb, ram_mb=ram_mb)
            self._log_reservation_event("resource_reserved", model_id, vram_mb, ram_mb)
        except Exception as e:
            logger.error(f"Error handling RESOURCE_RESERVED event: {e}", exc_info=True)

    async def _on_resource_released(self, event: Any) -> None:
        """
        Handle RESOURCE_RELEASED event.

        Always decrements reservation counter. Effective availability is derived:
        effective = gateway_reported - reserved
        """
        try:
            gateway_name, model_id, vram_mb, ram_mb, reason = (
                self._parse_reservation_event(event)
            )
            if not self._is_for_this_gateway(gateway_name):
                return

            self._state.release_reservation(vram_mb=vram_mb, ram_mb=ram_mb)
            self._log_reservation_event(
                "resource_released", model_id, vram_mb, ram_mb, reason
            )
        except Exception as e:
            logger.error(f"Error handling RESOURCE_RELEASED event: {e}", exc_info=True)

    # =========================================================================
    # Queries (Rare, On-Demand)
    # =========================================================================

    async def query(
        self,
        query_type: str,
        params: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """
        Send a query to Gateway and wait for response.

        This is rare - most data is available from cache.
        Use for on-demand data that's not in INIT message.

        Args:
            query_type: Query type (e.g., "get_model_config")
            params: Query parameters
            timeout: Response timeout in seconds

        Returns:
            Response data

        Raises:
            TimeoutError: If no response within timeout
            RuntimeError: If not connected

        Race-safe: captures ws snapshot before sending
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to Gateway")

        return await self._query_manager.query(
            self._connection.ws, query_type, params, timeout
        )

    # =========================================================================
    # Event Callbacks
    # =========================================================================

    def on_connected(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Set callback for connection established."""
        self._on_connected = callback

    def on_disconnected(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Set callback for connection lost."""
        self._on_disconnected = callback

    def on_model_loading_started(
        self, callback: Callable[[str], Awaitable[None]]
    ) -> None:
        """Set callback for model loading started event."""
        self._on_model_loading_started = callback

    def on_model_loaded(self, callback: Callable[[str, dict], Awaitable[None]]) -> None:
        """Set callback for model loaded event."""
        logger.debug(
            f"🔔 GatewayWebSocketClient: Setting on_model_loaded callback to {callback}"
        )
        self._on_model_loaded = callback
        logger.debug(
            f"✅ GatewayWebSocketClient: on_model_loaded callback set, "
            f"now: {self._on_model_loaded}"
        )

    def on_model_unloaded(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Set callback for model unloaded event."""
        self._on_model_unloaded = callback

    def on_model_load_failed(
        self, callback: Callable[[str, str], Awaitable[None]]
    ) -> None:
        """Set callback for model load failed event."""
        self._on_model_load_failed = callback

    def on_model_busy(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Set callback for model busy event."""
        self._on_model_busy = callback

    def on_model_idle(self, callback: Callable[[str, dict], Awaitable[None]]) -> None:
        """
        Set callback for model idle event.

        Callback receives (model_id, data) where data contains last_inference_time.
        """
        self._on_model_idle = callback

    def on_resource_update(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set callback for resource update event."""
        self._on_resource_update = callback

    def on_catalog_update(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set callback for catalog update event."""
        self._on_catalog_update = callback

    def on_telemetry_heartbeat(
        self, callback: Callable[[dict], Awaitable[None]]
    ) -> None:
        """Set callback for telemetry heartbeat event."""
        self._on_telemetry_heartbeat = callback

    # =========================================================================
    # Model-Specific Callback Registration (for LoadOutcomeTracker)
    # =========================================================================

    def register_model_load_callback(
        self,
        routing_key: str,
        on_loaded: Callable[[str, dict], Awaitable[None]] | None = None,
        on_failed: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        """
        Register callbacks for a specific model's load outcome.

        Used by LoadOutcomeTracker for concurrent load tracking.
        Multiple trackers can safely register for the same model -
        all callbacks will be invoked when the event arrives.

        Args:
            routing_key: Model routing key to track
            on_loaded: Callback when model loads successfully
            on_failed: Callback when model fails to load
        """
        if on_loaded:
            if routing_key not in self._model_loaded_callbacks:
                self._model_loaded_callbacks[routing_key] = set()
            self._model_loaded_callbacks[routing_key].add(on_loaded)
            logger.debug(f"Registered model_loaded callback for {routing_key}")
        if on_failed:
            if routing_key not in self._model_load_failed_callbacks:
                self._model_load_failed_callbacks[routing_key] = set()
            self._model_load_failed_callbacks[routing_key].add(on_failed)
            logger.debug(f"Registered model_load_failed callback for {routing_key}")

    def unregister_model_load_callback(
        self,
        routing_key: str,
        on_loaded: Callable[[str, dict], Awaitable[None]] | None = None,
        on_failed: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        """
        Unregister specific callbacks for a model.

        Only removes the exact callbacks provided, not all callbacks for the key.
        Safe to call even if not registered.

        Args:
            routing_key: Model routing key
            on_loaded: Specific loaded callback to remove
            on_failed: Specific failed callback to remove
        """
        removed = False
        if on_loaded and routing_key in self._model_loaded_callbacks:
            self._model_loaded_callbacks[routing_key].discard(on_loaded)
            if not self._model_loaded_callbacks[routing_key]:
                del self._model_loaded_callbacks[routing_key]
            removed = True
        if on_failed and routing_key in self._model_load_failed_callbacks:
            self._model_load_failed_callbacks[routing_key].discard(on_failed)
            if not self._model_load_failed_callbacks[routing_key]:
                del self._model_load_failed_callbacks[routing_key]
            removed = True
        if removed:
            logger.debug(f"Unregistered model load callback for {routing_key}")
