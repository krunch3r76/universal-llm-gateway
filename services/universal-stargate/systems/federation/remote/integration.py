"""
Remote mode setup.

Remote Stargate connects TO Master for federated inference.
"""

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from universal_logging import get_logger

from ..common.config import StargateMode
from ..common.connection_manager import ConnectionManager

if TYPE_CHECKING:
    from gateway_websocket import GatewayWebSocketClient

    from ..common.config import FederationConfig
    from ..common.health.status import FederationHealthHandler
    from ..link.ws.local import LocalEdgeClient
    from .api.request_store import ActiveRequestStore

logger = get_logger(__name__)


class RemoteIntegration:
    """
    Encapsulates Remote mode setup and callbacks.

    Manages:
    - Connection to Master
    - Connection to local Edge (if configured)
    - Gateway telemetry wiring
    - Reconnection handling
    """

    def __init__(self, config: "FederationConfig"):
        self._config = config
        self._connection_manager: ConnectionManager | None = None
        self._gateway_ws_client: Any | None = None
        self._gateway_url: str | None = None
        self._periodic_telemetry_task: Any | None = None  # asyncio.Task
        self._local_edge_client: LocalEdgeClient | None = None
        # Request store for cancel propagation (shared with inference router)
        self._request_store: ActiveRequestStore | None = None

    async def setup(
        self,
        app: FastAPI,
        gateway_manager: Any | None = None,
        model_manager: Any | None = None,
    ) -> "FederationHealthHandler":
        """
        Setup Remote mode.

        Args:
            app: FastAPI application
            gateway_manager: Optional gateway manager for telemetry endpoint
            model_manager: Optional model manager for token counting orchestration

        Returns:
            Initialized FederationHealthHandler
        """
        from ..common.health.status import FederationHealthHandler

        # Create request store early (needed for cancel callback)
        from .api.request_store import ActiveRequestStore

        self._request_store = ActiveRequestStore()

        self._configure_app_state(app, gateway_manager)
        self._init_remote_telemetry()
        await self._start_connection_manager()

        # Connect to local Edge Stargate if configured
        if self._config.local_edge:
            await self._start_local_edge_client()

        self._mount_remote_routes(app, gateway_manager)

        health_handler = FederationHealthHandler(self._config)

        # Determine role description
        role_desc = (
            "Relay/Edge Stargate" if self._config.local_edge else "Relay Stargate"
        )
        telemetry_mode = "http_polling" if self._config.disable_websocket else "ws"
        logger.info(
            f"✅ {role_desc} initialized "
            f"(mode=remote, stargate_id={self._config.stargate_id}, "
            f"telemetry={telemetry_mode})"
        )
        return health_handler

    def _configure_app_state(self, app: FastAPI, gateway_manager: Any | None) -> None:
        """Configure app.state with federation config and gateway manager."""
        app.state.federation_config = self._config

        # CRITICAL: Use 'is not None' - SingleGatewayManager.__bool__ returns False
        # when gateway not connected, but we want to set app.state regardless
        if gateway_manager is not None:
            app.state.gateway_manager = gateway_manager
            logger.info("✅ Gateway manager added to app state for telemetry endpoint")
        else:
            logger.warning(
                "⚠️ Gateway manager not provided - telemetry endpoint may "
                "fail until connected"
            )

    def _init_remote_telemetry(self) -> None:
        """Initialize Remote telemetry tracker."""
        from .api.telemetry import initialize_telemetry

        telemetry_log_level = self._config.telemetry_log_level
        initialize_telemetry(
            node_id=self._config.stargate_id,
            log_level=str(telemetry_log_level.value),
        )
        logger.info(
            f"Remote telemetry initialized: node_id={self._config.stargate_id}, "
            f"log_level={telemetry_log_level.value}"
        )

    async def _start_connection_manager(self) -> None:
        """Create and start ConnectionManager (connects TO Master)."""
        self._connection_manager = ConnectionManager(
            self._config,
            on_telemetry=None,  # Remote sends telemetry, doesn't receive
            on_cancel=self._handle_cancel_request,
            on_peer_connected=self._handle_peer_connected,
            on_peer_disconnected=self._handle_peer_disconnected,
        )
        await self._connection_manager.start()

    async def _handle_cancel_request(
        self, request_id: str, model_id: str | None
    ) -> bool:
        """
        Handle cancel request from Master via WebSocket.

        Cancels the local request in request_store.

        Args:
            request_id: The correlation ID of the request to cancel
            model_id: Optional model ID (unused, for interface compatibility)

        Returns:
            True if request was found and cancelled, False otherwise
        """
        if not self._request_store:
            logger.warning("Cannot cancel: request store not initialized")
            return False

        # In Remote, requests are keyed by request_id (from X-Correlation-ID header)
        success = self._request_store.cancel(request_id)
        if success:
            logger.info(f"🛑 Cancelled request {request_id[:8]}... via WebSocket")
        else:
            logger.debug(
                f"Cancel request {request_id[:8]}... not found "
                "(may already be completed)"
            )
        return success

    async def _start_local_edge_client(self) -> None:
        """Connect to local Edge Stargate over Unix socket."""
        from ..link.ws.local import LocalEdgeClient

        if not self._config.local_edge:
            return

        # CRITICAL: Pass Relay's stargate_id for auth (not Edge's)
        # Edge validates against allowed_peers keyed by Relay ID
        self._local_edge_client = LocalEdgeClient(
            config=self._config.local_edge,
            relay_stargate_id=self._config.stargate_id,
            on_telemetry=self._forward_edge_telemetry,
            on_connected=self._on_edge_connected,
            on_disconnected=self._on_edge_disconnected,
        )
        await self._local_edge_client.connect()
        logger.info(
            f"Started LocalEdgeClient for {self._config.local_edge.stargate_id}"
        )

    async def _forward_edge_telemetry(
        self, peer_id: str, msg_type: str, data: dict
    ) -> None:
        """
        Forward Edge telemetry to Master (Relay Stargates only).

        Delegates to RemoteTelemetrySender which handles:
        - Rate limiting (aggregation point)
        - Readiness gating
        - Queue clearing on disconnect

        Invariants (REL-TEL-01, REL-TEL-02):
        - ∀ event ∈ Edge telemetry: received_by_relay ⟹ forwarded_to_master
        - ∀ forwarding_failure: logged_at_ERROR_level

        Args:
            peer_id: Edge stargate_id
            msg_type: Telemetry message type (resource_update, model_loaded, etc.)
            data: Telemetry data
        """
        # Also ingest Edge telemetry for HTTP polling mode.
        #
        # In relay topology (Remote with local_edge), Master polls this Relay over HTTP:
        #   GET /api/v1/federation/telemetry
        # The telemetry endpoint must be backed by Edge state, not a local Gateway.
        try:
            from .api.telemetry import ingest_edge_telemetry

            ingest_edge_telemetry(msg_type=msg_type, data=data)
        except Exception as e:
            logger.error(
                "Edge telemetry ingestion failed",
                extra={"msg_type": msg_type, "peer_id": peer_id, "error": str(e)},
                exc_info=True,
            )

        from ..link.ws.remote.client import RemoteWebSocketClient

        logger.debug(
            "Forwarding telemetry to Master",
            extra={
                "msg_type": msg_type,
                "peer_id": peer_id,
                "gateway_id": data.get("gateway_id"),
            },
        )

        # Check connection manager
        if not self._connection_manager:
            logger.error(
                "Telemetry dropped - ConnectionManager not initialized",
                extra={
                    "msg_type": msg_type,
                    "peer_id": peer_id,
                    "drop_reason": "connection_manager_missing",
                },
            )
            return

        # Check remote client type
        remote_client = self._connection_manager.remote_client
        if not isinstance(remote_client, RemoteWebSocketClient):
            logger.error(
                "Telemetry dropped - Not WebSocket client",
                extra={
                    "msg_type": msg_type,
                    "peer_id": peer_id,
                    "client_type": type(remote_client).__name__,
                    "drop_reason": "wrong_client_type",
                },
            )
            return

        # Check telemetry sender
        telemetry_sender = remote_client.telemetry_sender
        if not telemetry_sender:
            logger.error(
                "Telemetry dropped - RemoteTelemetrySender not available",
                extra={
                    "msg_type": msg_type,
                    "peer_id": peer_id,
                    "drop_reason": "telemetry_sender_missing",
                },
            )
            return

        # Forward to Master via rate-limited sender with readiness gating
        # Sender handles queue clearing on disconnect and readiness checks
        try:
            await telemetry_sender.forward_edge_telemetry(peer_id, msg_type, data)

            logger.debug(
                "Telemetry enqueued for Master",
                extra={
                    "msg_type": msg_type,
                    "peer_id": peer_id,
                    "gateway_id": data.get("gateway_id"),
                },
            )
        except Exception as e:
            logger.error(
                "Telemetry forwarding failed",
                extra={"msg_type": msg_type, "peer_id": peer_id, "error": str(e)},
                exc_info=True,
            )

    async def _on_edge_connected(self) -> None:
        """
        Handle Edge connection or reconnection.

        Telemetry forwarding is automatically active via on_telemetry callback.
        No additional wiring needed (bound method pattern).
        """
        logger.info("✅ Connected to Edge Stargate - telemetry forwarding active")

    async def _on_edge_disconnected(self) -> None:
        """
        Handle Edge disconnection.

        Clears stale model state from the HTTP polling telemetry cache and
        forwards synthetic MODEL_UNLOADED events to the Master so it removes
        the models from routing.

        Telemetry forwarding will resume automatically on reconnection
        (callback persists across connection cycles).
        """
        logger.warning(
            "⚠️ Disconnected from Edge Stargate - telemetry forwarding paused"
        )

        from .api.telemetry import handle_edge_disconnected

        previously_loaded = handle_edge_disconnected()

        edge_id = (
            self._config.local_edge.stargate_id if self._config.local_edge else None
        )
        for model_id in previously_loaded:
            logger.info(
                f"Forwarding synthetic MODEL_UNLOADED for {model_id} "
                f"(edge disconnected)"
            )
            await self._forward_edge_telemetry(
                peer_id=edge_id or "unknown",
                msg_type="telemetry.model.unloaded",
                data={"model_id": model_id},
            )

    def _mount_remote_routes(self, app: FastAPI, gateway_manager: Any | None) -> None:
        """Mount all Remote API routes."""
        from .api.cancel import create_cancel_router
        from .api.inference import create_inference_router
        from .api.models import create_model_router
        from .api.telemetry import router as telemetry_router
        from .api.tokens import create_federation_token_router

        # Model load endpoint (Master commands loads)
        # Pass local_edge_client for relay topology forwarding
        app.include_router(
            create_model_router(
                gateway_manager=gateway_manager,
                local_edge_client=self._local_edge_client,
                relay_stargate_id=self._config.stargate_id,
            )
        )

        # Token counting endpoint
        token_router = create_federation_token_router(
            config=self._config,
            gateway_socket_path=None,
            gateway_url=None,
            local_edge_client=self._local_edge_client,  # Pass for relay forwarding
        )
        app.include_router(token_router)

        # Telemetry endpoint (for HTTP polling by Master)
        app.include_router(telemetry_router)
        logger.info(
            "Registered HTTP telemetry endpoint: GET /api/v1/federation/telemetry"
        )

        # Use shared request_store (created in setup() for cancel callback wiring)
        # INVARIANT: setup() creates _request_store before calling _mount_remote_routes
        from .api.request_store import ActiveRequestStore

        assert self._request_store is not None, (
            "request_store must be created in setup() before _mount_remote_routes()"
        )
        request_store: ActiveRequestStore = self._request_store

        # Inference endpoint (for Master to forward requests)
        app.include_router(
            create_inference_router(
                self._config,
                request_store=request_store,
                gateway_socket_path=None,
                gateway_url=None,
                local_edge_client=self._local_edge_client,
                gateway_id=None,
            )
        )

        # Cancel endpoint (shares request_store)
        app.include_router(create_cancel_router(request_store))

    @property
    def connection_manager(self) -> ConnectionManager | None:
        return self._connection_manager

    async def _handle_peer_connected(self, peer_id: str) -> None:
        """
        Handle connection to Master.

        Telemetry forwarding resumes automatically via readiness gating.
        Sends snapshot to sync Master state after reconnection.
        """
        logger.info(f"✅ Master {peer_id} connected - telemetry forwarding active")

        if self._config.mode == StargateMode.REMOTE:
            await self._resend_telemetry_after_reconnect()

    async def _handle_peer_disconnected(self, peer_id: str) -> None:
        """
        Handle disconnection from Master.

        Telemetry forwarding paused via readiness gating. Queue cleared automatically
        by sender. Edge telemetry will be enqueued but not sent until reconnection.
        """
        logger.warning(
            f"⚠️ Master {peer_id} disconnected - telemetry paused "
            "(queue will be cleared)"
        )

    async def _resend_telemetry_after_reconnect(self) -> None:
        """Resend telemetry snapshot after WebSocket reconnection."""
        import asyncio

        from ..link.ws.remote.client import RemoteWebSocketClient

        # CRITICAL: Uses Phase 1's snapshot helpers
        from .telemetry.snapshot import build_telemetry_payload, log_snapshot_sent

        if not self._connection_manager:
            logger.warning("Cannot resend telemetry: ConnectionManager not initialized")
            return

        remote_client = self._connection_manager.remote_client
        if not isinstance(remote_client, RemoteWebSocketClient):
            logger.warning("Cannot resend telemetry: not a RemoteWebSocketClient")
            return

        telemetry_sender = remote_client.telemetry_sender
        if not telemetry_sender:
            logger.warning(
                "Cannot resend telemetry: RemoteTelemetrySender not available"
            )
            return

        if not self._gateway_url or not self._gateway_ws_client:
            logger.warning("Cannot resend telemetry: gateway not wired")
            return

        ws_client = self._gateway_ws_client
        if not ws_client.is_connected:
            logger.warning("Cannot resend telemetry: Gateway not connected")
            return

        # INVARIANT: Reconnect telemetry applies filtering
        payload = build_telemetry_payload(ws_client, apply_filtering=True)

        _ = asyncio.create_task(
            telemetry_sender.on_resource_update(payload),
            name="federation-telemetry-reconnect",
        )

        log_snapshot_sent(
            "🔄 Resent telemetry after reconnect",
            len(payload["available_models"]),
            len(ws_client.get_models()),
            len(payload["loaded_models"]),
            len(payload["busy_models"]),
        )

    def wire_gateway_telemetry(
        self,
        ws_client: "GatewayWebSocketClient",
        gateway_url: str,
    ) -> None:
        """
        Wire local Gateway WebSocket callbacks to RemoteTelemetrySender.

        CRITICAL: Enables telemetry flow from local Gateway → Master.
        """
        from .telemetry.wiring import wire_telemetry_callbacks

        if self._config.mode != StargateMode.REMOTE:
            logger.debug("Skipping gateway telemetry wiring (not in REMOTE mode)")
            return

        if not self._connection_manager:
            logger.warning("Cannot wire telemetry: ConnectionManager not initialized")
            return

        from ..link.ws.remote.client import RemoteWebSocketClient

        remote_client = self._connection_manager.remote_client
        if not isinstance(remote_client, RemoteWebSocketClient):
            logger.warning("Cannot wire telemetry: not a RemoteWebSocketClient")
            return

        telemetry_sender = remote_client.telemetry_sender
        if not telemetry_sender:
            logger.warning("Cannot wire telemetry: RemoteTelemetrySender not available")
            return

        # Cache for reconnection
        self._gateway_ws_client = ws_client
        self._gateway_url = gateway_url

        # Wire callbacks using helper (returns periodic task for cancellation)
        self._periodic_telemetry_task = wire_telemetry_callbacks(
            ws_client, gateway_url, telemetry_sender
        )

        logger.info(
            f"✅ Gateway telemetry wired to federation (gateway_url={gateway_url})"
        )

    async def shutdown(self) -> None:
        """Shutdown Remote mode components."""
        # Stop local edge client
        if self._local_edge_client:
            await self._local_edge_client.disconnect()
            self._local_edge_client = None

        # Cancel periodic telemetry task
        if self._periodic_telemetry_task:
            self._periodic_telemetry_task.cancel()
            try:
                await self._periodic_telemetry_task
            except Exception:
                pass  # Task cancellation expected
            self._periodic_telemetry_task = None

        # Stop connection manager
        if self._connection_manager:
            await self._connection_manager.stop()
            self._connection_manager = None
