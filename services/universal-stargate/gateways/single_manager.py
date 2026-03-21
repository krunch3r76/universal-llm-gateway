"""
Single Gateway Manager for 1:1 Stargate-Gateway relationship.

Replaces MultiGatewayManager. Routing happens at Stargate level via federation.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_logging import get_logger

from gateway_client import GatewayClient, ModelMetadata

from .connection_validation import (
    GatewayConnectionError,
    fetch_and_validate_gateway_connection,
)
from .queue import ExecutionCompletionWaiter
from .types import GatewayInstance

if TYPE_CHECKING:
    from collections.abc import Callable
    from gateway_client import GatewayConfig

logger = get_logger(__name__)


class GatewayUnavailableError(Exception):
    """Raised when local gateway is not connected."""

    pass


class SingleGatewayManager:
    """
    Manager for the single local gateway (1:1 with Stargate).

    Invariant: |local_gateways| = 1
    """

    def __init__(
        self,
        gateway_config: GatewayConfig,
        event_bus: Any = None,
    ) -> None:
        """
        Initialize single gateway manager.

        Args:
            gateway_config: Configuration for the local gateway
            event_bus: Event bus for state notifications
        """
        self.gateway_config = gateway_config
        self.event_bus = event_bus

        self.gateway: GatewayInstance | None = None

        # Split caches by semantic type to prevent type confusion
        # Use ModelId keys for type-safe normalized comparison
        self._model_info_cache: dict[ModelId, dict[str, Any]] = {}
        self._model_configuration_cache: dict[ModelId, ModelMetadata] = {}
        self._cache_timestamp = time.time()

        self._initialized = False

        # ModelRouter (lazy-initialized in _ensure_model_router)
        self._model_router: Any = None  # Type: ModelRouter

        # Stored config for ModelRouter initialization (set via set_config)
        self._config: dict[str, Any] | None = None

        # Federation manager (injected via set_federated_manager)
        self._federated_manager: Any = None  # Type: FederatedGatewayManager

        # ExecutionCompletionWaiter for event-driven execution completion waiting
        self._execution_completion_waiter = ExecutionCompletionWaiter(
            event_bus=event_bus
        )

        # Model sync callback for coordinator synchronization
        self._model_sync_callback: Callable[[str, frozenset[str]], None] | None = None

    @property
    def execution_completion_waiter(self) -> ExecutionCompletionWaiter:
        """Event-driven waiter for model.execution.completed signals."""
        return self._execution_completion_waiter

    # ----- Config Management -----

    def set_config(self, config: dict[str, Any]) -> None:
        """
        Store config for ModelRouter initialization.

        MUST be called before first access to model_router if federation is enabled.

        Args:
            config: Full Stargate config dict containing federation.stargate_id
        """
        self._config = config

    def _require_config(self) -> dict[str, Any]:
        """
        Get stored config or fail-fast.

        Raises:
            RuntimeError: If config not set and federation is enabled
        """
        if self._config is not None:
            return self._config

        # Allow empty config only if federation is NOT enabled
        if self._federated_manager is not None:
            raise RuntimeError(
                "SingleGatewayManager.set_config() must be called before "
                "accessing model_router when federation is enabled. "
                "Config is required to extract federation.stargate_id."
            )

        return {}

    # ----- ModelRouter Integration -----

    async def await_execution_completion(
        self, model_id: str, timeout_s: float | None = None
    ) -> GatewayInstance:
        """
        Wait for model.execution.completed event and return gateway.

        IMPORTANT: This method is called AFTER immediate routing failed.
        The caller has already verified the gateway doesn't have capacity.
        We MUST wait for a model.execution.completed event (execution finished).

        Do NOT short-circuit by checking get_gateway() - that checks connectivity,
        not capacity. The gateway may be connected but busy with another model.

        Args:
            model_id: Model ID (for logging/context only)
            timeout_s: Optional timeout in seconds

        Raises:
            GatewayUnavailableError: Gateway not available after wait
            TimeoutError: Timeout expires
        """
        # Wait for execution completion signal (execution completed or model unloaded)
        # No fast path - caller already tried immediate routing and it failed
        logger.info(f"⏳ Waiting for execution completion for {model_id}")
        await self._execution_completion_waiter.wait_for_execution_completion(
            timeout_s=timeout_s
        )

        # Recheck after event
        gateway = self.get_gateway()
        if gateway:
            logger.info(f"✅ Execution completed for {model_id}")
            return gateway

        raise GatewayUnavailableError(
            f"Gateway not available for {model_id} after waiting"
        )

    def _get_federation_stargate_id(self, config: dict[str, Any]) -> str | None:
        """
        Extract federation.stargate_id from config.

        Returns:
            stargate_id if federation enabled and configured, None otherwise
        """
        if self._federated_manager is None:
            return None

        fed_config = config.get("federation", {})
        stargate_id = fed_config.get("stargate_id")

        if not stargate_id:
            raise ValueError(
                "federation.stargate_id is required when federation is enabled. "
                "Configure federation.stargate_id in stargate_config.yaml"
            )

        return stargate_id

    def _build_model_router(self, config: dict[str, Any]) -> Any:
        """
        Build ModelRouter with proper federation configuration.

        Args:
            config: Full Stargate config dict

        Returns:
            Configured ModelRouter instance
        """
        from systems.routing.model_router import ModelRouter

        local_stargate_id = self._get_federation_stargate_id(config)

        return ModelRouter(
            gateway_manager=self,
            config=config,
            event_bus=self.event_bus,
            federated_manager=self._federated_manager,
            local_stargate_id=local_stargate_id,
        )

    def _ensure_model_router(self) -> Any:
        """
        Ensure ModelRouter is initialized (lazy initialization).

        Uses stored config from set_config(). Fails fast if federation
        is enabled but config was not set.
        """
        if self._model_router is None:
            config = self._require_config()
            self._model_router = self._build_model_router(config)
            logger.debug("ModelRouter initialized")
        return self._model_router

    @property
    def model_router(self) -> Any:
        """
        Get ModelRouter instance (lazy-initialized).

        Raises:
            RuntimeError: If federation enabled but set_config() not called
        """
        if self._model_router is None:
            self._ensure_model_router()
        return self._model_router

    def set_model_sync_callback(
        self, callback: Callable[[str, frozenset[str]], None]
    ) -> None:
        """Register callback for model load/unload events."""
        self._model_sync_callback = callback
        logger.debug("Model sync callback registered")

        # Wire callback to existing gateway if already initialized
        if self.gateway and self.gateway.client:
            self._wire_websocket_callbacks()

    def set_federated_manager(self, manager: Any, config: dict[str, Any]) -> None:
        """
        Inject FederatedGatewayManager for federation-aware routing.

        CRITICAL: Configures existing ModelRouter in-place (does NOT reset).

        Args:
            manager: FederatedGatewayManager instance
            config: Config dict containing federation.stargate_id (REQUIRED)

        Raises:
            ValueError: If config missing federation.stargate_id
        """
        self._federated_manager = manager

        # Store config for future router initialization
        self._config = config

        # Extract stargate_id (fail-fast validation)
        fed_config = config.get("federation", {})
        local_stargate_id = fed_config.get("stargate_id")

        if not local_stargate_id:
            raise ValueError(
                "federation.stargate_id is required when setting federated_manager. "
                "Configure federation.stargate_id in stargate_config.yaml"
            )

        # Configure existing router (don't reset!)
        if self._model_router is not None:
            self._model_router.configure_federation(manager, local_stargate_id)
            logger.info(
                f"Federation configured on existing ModelRouter: "
                f"stargate_id={local_stargate_id}"
            )
        else:
            logger.debug(
                f"FederatedGatewayManager stored for ModelRouter init: "
                f"stargate_id={local_stargate_id}"
            )

    def _wire_websocket_callbacks(self) -> None:
        """Wire model sync callback to WebSocket client events."""
        if not self._model_sync_callback or not self.gateway:
            return

        gateway_name = self.gateway.config.name
        ws_client = self.gateway.client._ws_client

        # Get current loaded models for immediate sync
        current_loaded = ws_client.get_loaded_models()

        # Create wrapper callbacks that invoke the sync callback
        async def on_model_loaded_wrapper(
            _model_id: str, _data: dict[str, Any]
        ) -> None:
            """Wrapper for MODEL_LOADED event."""
            if self._model_sync_callback:
                loaded_models = ws_client.get_loaded_models()
                self._model_sync_callback(gateway_name, loaded_models)

        async def on_model_unloaded_wrapper(_model_id: str) -> None:
            """Wrapper for MODEL_UNLOADED event."""
            if self._model_sync_callback:
                loaded_models = ws_client.get_loaded_models()
                self._model_sync_callback(gateway_name, loaded_models)

        # Register WebSocket event callbacks
        ws_client.on_model_loaded(on_model_loaded_wrapper)
        ws_client.on_model_unloaded(on_model_unloaded_wrapper)

        logger.debug(f"WebSocket callbacks wired for gateway: {gateway_name}")

        # Initial sync with current state
        self._model_sync_callback(gateway_name, current_loaded)

    # ----- Dual Accessor Pattern -----

    def get_gateway(self) -> GatewayInstance | None:
        """Get local gateway if connected, None otherwise (for routing decisions)."""
        if self.gateway and self.gateway.client.is_connected():
            return self.gateway
        return None

    def require_gateway(self) -> GatewayInstance:
        """Get local gateway or raise GatewayUnavailableError if disconnected."""
        gw = self.get_gateway()
        if gw is None:
            raise GatewayUnavailableError(
                "Local gateway is not connected. Check gateway health and connectivity."
            )
        return gw

    # ----- Lifecycle -----

    async def initialize(self) -> None:
        """Initialize gateway connection."""
        if self._initialized:
            logger.debug("Already initialized, skipping")
            return

        # Diagnostic logging
        logger.info(
            f"SingleGatewayManager.initialize(): name={self.gateway_config.name}, "
            f"socket_path={self.gateway_config.socket_path}, "
            f"base_url={self.gateway_config.base_url}"
        )

        # Create gateway instance
        client = GatewayClient(
            config=self.gateway_config,
            event_bus=self.event_bus,
        )

        self.gateway = GatewayInstance(
            config=self.gateway_config,
            client=client,
        )

        # Connect
        try:
            await client.connect()
            logger.info(f"✅ Connected to gateway: {self.gateway_config.base_url}")

            # Validate we're connected to Gateway (not another Stargate)
            # INVARIANT: Edge Stargate → Gateway (never Stargate → Stargate)
            await fetch_and_validate_gateway_connection(client)

        except GatewayConnectionError as e:
            # Architecture violation - fail fast
            logger.critical(f"Gateway connection validation failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to connect to gateway: {e}")
            # Gateway will auto-reconnect

        self._initialized = True

        # Wire model sync callback if already registered
        if self._model_sync_callback:
            self._wire_websocket_callbacks()

        logger.info("SingleGatewayManager initialized")

    async def shutdown(self) -> None:
        """Shutdown gateway connection."""
        if self.gateway:
            try:
                await self.gateway.client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting gateway: {e}")

        self.gateway = None
        logger.info("SingleGatewayManager shutdown complete")

    # ----- Model Info & Configuration -----

    async def fetch_model_info_dict(
        self, model_id: ModelId, *, include_all_fields: bool = False
    ) -> dict[str, Any] | None:
        """
        Fetch raw model info dict (OpenAI-ish model object) from Gateway.

        Returns arbitrary fields from Gateway's model catalog. Use this for
        features requiring fields not in ModelMetadata (e.g., personality config).

        For typed resource requirements (vram_usage, ram_usage), use
        fetch_model_configuration() instead.

        Args:
            model_id: Model ID object (normalized internally)
            include_all_fields: Include all fields from catalog

        Returns:
            Dict with arbitrary fields if found, None if not found or unavailable

        Invariant:
            cache_key = ModelId (normalized)
            api_call = synthetic_id for -cpu models, catalog_lookup_id for others
        """
        # Check cache with ModelId key
        if model_id in self._model_info_cache:
            return self._model_info_cache[model_id]

        gateway = self.get_gateway()
        if not gateway:
            return None

        try:
            # API call uses appropriate lookup ID:
            # - -cpu models: synthetic_id (gateway stores them WITH -cpu suffix)
            # - others: catalog_lookup_id (strips -hybrid which is informational)
            api_lookup_id = (
                model_id.synthetic_id if model_id.is_cpu else model_id.catalog_lookup_id
            )

            info = await gateway.client.fetch_model_info_dict(
                api_lookup_id, include_all_fields=include_all_fields
            )
            if info:
                # Cache with ModelId key
                self._model_info_cache[model_id] = info
            return info
        except Exception as e:
            logger.error(
                f"Error fetching model info for {model_id}: {e}", exc_info=True
            )
            return None

    def model_exists_in_federation(self, model_id: str) -> bool:
        """
        Check if model exists in any federated gateway catalog.

        Lightweight check for federation-aware model existence.
        Does NOT return metadata - routing will fetch from selected gateway.

        Args:
            model_id: Model ID to check

        Returns:
            True if model exists on any healthy federated gateway
        """
        if not self._federated_manager:
            return False

        parsed_model_id = ModelId.parse(model_id)

        for fed_gw in self._federated_manager.get_healthy_gateways():
            if parsed_model_id in fed_gw.available_models:
                logger.debug(
                    f"Model {model_id} found in federated gateway {fed_gw.gateway_id}"
                )
                return True

        return False

    async def fetch_model_configuration(
        self, model_id: ModelId
    ) -> ModelMetadata | None:
        """
        Fetch typed model configuration (ModelMetadata) from LOCAL gateway only.

        Returns ModelMetadata with typed fields (vram_usage, ram_usage, format, etc).
        Use this for resource requirements and routing decisions.

        For arbitrary fields (e.g., personality config), use fetch_model_info_dict().

        For federated models, use model_exists_in_federation() to check existence,
        then fetch configuration from the selected gateway after routing.

        Args:
            model_id: Model ID object (normalized internally)

        Returns:
            ModelMetadata if found locally, None otherwise

        Invariant:
            cache_key = ModelId (normalized)
            api_call = synthetic_id for -cpu models (gateway stores WITH -cpu suffix)
                       catalog_lookup_id for others
                       (strips -hybrid which is informational)
        """
        # Check cache with ModelId key
        if model_id in self._model_configuration_cache:
            return self._model_configuration_cache[model_id]

        gateway = self.get_gateway()
        if not gateway:
            return None

        try:
            # API call uses appropriate lookup ID:
            # - -cpu models: synthetic_id (gateway stores them WITH -cpu suffix)
            # - others: catalog_lookup_id (strips -hybrid which is informational)
            api_lookup_id = (
                model_id.synthetic_id if model_id.is_cpu else model_id.catalog_lookup_id
            )

            config = await gateway.client.fetch_model_configuration(api_lookup_id)
            if config:
                # Cache with ModelId key
                self._model_configuration_cache[model_id] = config
            return config
        except Exception as e:
            logger.error(
                f"Error fetching model configuration for {model_id}: {e}", exc_info=True
            )
            return None

    def get_cached_model_configuration(self, model_id: ModelId) -> ModelMetadata | None:
        """Get cached model configuration without fetching.

        For synchronous cache lookup (e.g., monitoring paths that must not block).
        Uses ModelId comparison semantics (handles -hybrid/-cpu normalization).

        Args:
            model_id: Model ID object (normalized internally)

        Returns:
            Cached ModelMetadata if present, None otherwise

        Invariant: ∀ lookup: uses ModelId.__eq__ for comparison
        """
        return self._model_configuration_cache.get(model_id)

    def clear_model_cache(self, reason: str) -> None:
        """Clear both model info and configuration caches."""
        self._model_info_cache = {}
        self._model_configuration_cache = {}
        self._cache_timestamp = time.time()
        logger.info(f"🗑️  Model caches cleared (info + configuration): {reason}")

    # ----- Model Sets (for Pipeline Availability) -----

    def get_model_set(self) -> frozenset[str]:
        """
        Get models available on local gateway.

        Used for pipeline availability filtering.

        Returns:
            Set of model IDs from local gateway catalog
        """
        gateway = self.get_gateway()
        if not gateway:
            return frozenset()
        return frozenset(gateway.client.get_models())

    def iter_gateway_model_sets(self) -> list[set[str]]:
        """
        Get model sets from local gateway as a list.

        Compatibility method for pipeline availability filtering.
        Returns list with single set (1:1 Stargate:Gateway).

        Returns:
            List containing one set of model IDs, or empty list if disconnected
        """
        model_set = self.get_model_set()
        if not model_set:
            return []
        return [set(model_set)]

    async def fetch_model_ids(self) -> set[str]:
        """
        Fetch available model IDs from local gateway.

        Async for API consistency with potential future network operations.
        Currently wraps sync get_models() for 1:1 gateway relationship.

        Returns:
            Set of model ID strings from gateway catalog
        """
        gateway = self.get_gateway()
        if not gateway:
            return set()
        return set(gateway.client.get_models())

    # ----- Status -----

    def get_gateway_status(self) -> dict[str, Any]:
        """Get status of local gateway."""
        gateway = self.get_gateway()
        if not gateway:
            return {
                "connected": False,
                "status": "disconnected",
            }

        return {
            "connected": True,
            "status": "healthy",
            "base_url": gateway.config.base_url,
            "loaded_models": list(gateway.client.get_loaded_models()),
            "available_models": len(gateway.client.get_models()),
        }

    def get_gateway_status_full(self) -> dict[str, dict[str, Any]]:
        """
        Get full status including VRAM/RAM capacity and models.

        Returns:
            Dict mapping gateway name to full status dict with:
            - enabled: bool (always True for local gateway)
            - is_connected: bool
            - total_vram_mb: int
            - available_vram_mb: int
            - total_ram_mb: int
            - available_ram_mb: int
            - models: list[str] (available models)
            - loaded_models: list[str]
            - busy_models: list[str]
        """
        gateway = self.get_gateway()
        gateway_name = self.gateway_config.name

        if not gateway:
            return {
                gateway_name: {
                    "enabled": True,
                    "is_connected": False,
                    "total_vram_mb": 0,
                    "available_vram_mb": 0,
                    "total_ram_mb": 0,
                    "available_ram_mb": 0,
                    "models": [],
                    "loaded_models": [],
                    "busy_models": [],
                }
            }

        # Get resource status from WebSocket state
        ws_client = gateway.client._ws_client
        resource_status = ws_client.get_resource_status()

        return {
            gateway_name: {
                "enabled": True,
                "is_connected": True,
                "total_vram_mb": resource_status.total_vram_mb,
                "available_vram_mb": resource_status.available_vram_mb,
                "total_ram_mb": resource_status.total_ram_mb,
                "available_ram_mb": resource_status.available_ram_mb,
                "models": list(gateway.client.get_models()),
                "loaded_models": list(gateway.client.get_loaded_models()),
                "busy_models": list(ws_client.get_busy_models()),
            }
        }

    def __bool__(self) -> bool:
        """Return True if gateway is connected."""
        return self.get_gateway() is not None
