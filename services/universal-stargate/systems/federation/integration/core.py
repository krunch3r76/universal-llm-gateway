"""
Federation integration dispatcher.

Mode-specific setup delegated to domain modules.
"""

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from universal_logging import get_logger

from ..common.config import (
    FederationConfig,
    StargateMode,
    load_federation_config,
    log_startup_banner,
)
from ..common.metrics.prometheus import get_metrics

if TYPE_CHECKING:
    from gateway_websocket import GatewayWebSocketClient

    from ..common.health.status import FederationHealthHandler
    from ..edge.server import EdgeFederationServer
    from ..master.circuit_breaker import FederationCircuitBreaker
    from ..master.integration import MasterIntegration
    from ..master.manager.federated_gateway_manager import FederatedGatewayManager
    from ..master.routing.forward import FederatedRequestForwarder
    from ..remote.integration import RemoteIntegration

logger = get_logger(__name__)


class FederationIntegration:
    """
    Federation integration with mode-specific delegation.

    Lifecycle:
    1. Load and validate config
    2. Dispatch to master/ or remote/ integration modules
    3. Start mode-specific components
    """

    def __init__(
        self, config: FederationConfig | None = None, event_bus: Any | None = None
    ):
        self._config: FederationConfig = config or load_federation_config()
        self._started: bool = False
        self._event_bus: Any | None = event_bus

        # Mode-specific integration objects
        self._mode_integration: MasterIntegration | RemoteIntegration | None = None
        self._health_handler: FederationHealthHandler | None = None
        self._edge_server: EdgeFederationServer | None = None

        # Initialize metrics (creates prometheus collectors)
        from ..common.metrics.prometheus import FederationMetrics

        self._metrics: FederationMetrics = get_metrics()

    @property
    def config(self) -> FederationConfig:
        return self._config

    @property
    def federated_manager(self) -> "FederatedGatewayManager | None":
        if hasattr(self._mode_integration, "federated_manager"):
            return self._mode_integration.federated_manager
        return None

    @property
    def health_handler(self) -> "FederationHealthHandler | None":
        return self._health_handler

    @property
    def forwarder(self) -> "FederatedRequestForwarder | None":
        """Get FederatedRequestForwarder (Master mode only)."""
        if hasattr(self._mode_integration, "forwarder"):
            return self._mode_integration.forwarder
        return None

    @property
    def circuit_breaker(self) -> "FederationCircuitBreaker | None":
        """Get circuit breaker (Master mode only)."""
        if hasattr(self._mode_integration, "circuit_breaker"):
            return self._mode_integration.circuit_breaker
        return None

    @property
    def load_orchestrator(self) -> Any | None:
        """Get load orchestrator (Master mode only)."""
        if hasattr(self._mode_integration, "load_orchestrator"):
            return self._mode_integration.load_orchestrator
        return None

    @property
    def request_tracker(self) -> Any | None:
        """
        Get MasterRequestTracker for routing feasibility checks.

        Returns:
            MasterRequestTracker if in Master mode, None otherwise

        Note:
            Used by routing DecisionEngine to enforce compute-type worker limits.
        """
        if hasattr(self._mode_integration, "request_tracker"):
            return self._mode_integration.request_tracker
        return None

    async def startup(
        self,
        app: FastAPI,
        gateway_socket_path: str | None = None,
        model_manager: Any | None = None,
        gateway_manager: Any | None = None,
    ) -> None:
        """
        Start federation based on mode.

        Dispatches to master/ or remote/ integration modules.

        Args:
            app: FastAPI application
            gateway_socket_path: Optional gateway socket path for Master.
            model_manager: Optional model manager for Remote mode.
            gateway_manager: Optional gateway manager for Remote mode.
        """
        if self._started:
            return

        logger.info("🚀 Starting federation integration")

        log_startup_banner(self._config)

        if self._config.mode == StargateMode.MASTER:
            from ..master.integration import MasterIntegration

            self._mode_integration = MasterIntegration(
                self._config,
                event_bus=self._event_bus,
            )
            self._health_handler = await self._mode_integration.setup(
                app,
                gateway_socket_path=gateway_socket_path,
            )

        elif self._config.mode == StargateMode.REMOTE:
            from ..remote.integration import RemoteIntegration

            self._mode_integration = RemoteIntegration(self._config)
            self._health_handler = await self._mode_integration.setup(
                app,
                gateway_manager=gateway_manager,
                model_manager=model_manager,
            )

        else:
            # EDGE mode: Accept inbound federation connections
            from ..edge import EdgeFederationServer, create_edge_federation_router

            # DIAGNOSTIC: Log what we received
            logger.info(
                f"🔍 Edge mode setup: gateway_manager={gateway_manager}, "
                f"allowed_peers={self._config.allowed_peers}"
            )

            can_federate = bool(
                self._config.allowed_peers or not self._config.federation_auth_enabled
            )

            if gateway_manager is not None and can_federate:
                self._edge_server = EdgeFederationServer(self._config, gateway_manager)

                # Mount WebSocket endpoint for telemetry
                app.include_router(create_edge_federation_router(self._edge_server))

                # Mount HTTP federation endpoints (same as Remote mode)
                self._mount_edge_http_endpoints(app, gateway_manager)

                peer_ids = [p.stargate_id for p in self._config.allowed_peers]
                auth_label = "auth" if self._config.federation_auth_enabled else "open"
                logger.info(
                    f"Edge mode: /ws/federation/edge + HTTP endpoints mounted "
                    f"(allowed_peers={peer_ids}, {auth_label})"
                )
            elif gateway_manager is not None:
                logger.info("Edge mode: no allowed_peers - federation disabled")
            else:
                logger.warning("Edge mode: no gateway_manager - federation unavailable")

            self._started = True
            return

        self._started = True
        logger.info(
            f"✅ Federation integration started (mode={self._config.mode.value})"
        )

    async def shutdown(self) -> None:
        """Shutdown federation integration."""
        if not self._started:
            return

        logger.info("Shutting down federation integration")

        if self._mode_integration and hasattr(self._mode_integration, "shutdown"):
            await self._mode_integration.shutdown()

        self._started = False

    def _mount_edge_http_endpoints(self, app: FastAPI, gateway_manager: Any) -> None:
        """
        Mount HTTP federation endpoints for Edge mode.

        Edge exposes same HTTP endpoints as Remote for Master→Edge requests.
        """
        from ..remote.api.cancel import create_cancel_router
        from ..remote.api.inference import create_inference_router
        from ..remote.api.models import create_model_router
        from ..remote.api.request_store import ActiveRequestStore
        from ..remote.api.tokens import create_federation_token_router

        # Model load endpoint (Master commands loads)
        # Edge mode: No relay forwarding, always uses local gateway_manager
        app.include_router(
            create_model_router(
                gateway_manager=gateway_manager,
                local_edge_client=None,
            )
        )

        # Register gateway with tracker for slot management
        # Edge's gateway_id matches telemetry: {stargate_id}-gateway
        gateway_id = f"{self._config.stargate_id}-gateway"

        from src.core.gateway_tracker import gateway_tracker

        # Register with dummy host/port (Edge uses socket, not HTTP)
        gateway_tracker.register_gateway(gateway_id, "localhost", 0)
        logger.info(f"Registered gateway {gateway_id} with tracker for slot management")

        # Token counting endpoint
        # Edge mode: Extract gateway connection from gateway_manager
        gateway_socket = None
        gateway_url = None

        # CRITICAL: Use 'is not None' check, not truthiness check
        # SingleGatewayManager.__bool__ returns False if gateway not connected,
        # but we need the config even before connection
        has_gateway_config = (
            hasattr(gateway_manager, "gateway_config")
            if gateway_manager is not None
            else False
        )
        logger.info(
            f"🔍 Edge mode token router: gateway_manager={gateway_manager}, "
            f"has_gateway_config={has_gateway_config}"
        )

        if gateway_manager is not None and has_gateway_config:
            gateway_socket = gateway_manager.gateway_config.socket_path
            gateway_url = gateway_manager.gateway_config.base_url
            logger.info(
                f"🔍 Edge mode token router config: "
                f"socket={gateway_socket}, url={gateway_url}"
            )
        else:
            logger.warning(
                f"⚠️ Edge mode: Cannot extract gateway config - "
                f"gateway_manager={gateway_manager}, "
                f"has_gateway_config={has_gateway_config}"
            )

        # Pass gateway connection to token router
        # Edge mode uses direct Gateway connection (not federation.local_edge)
        token_router = create_federation_token_router(
            config=self._config,
            gateway_socket_path=gateway_socket,  # Pass actual gateway socket path
            gateway_url=gateway_url,  # Pass gateway URL for HTTP mode
        )
        app.include_router(token_router)

        # Inference endpoint (for Master to forward requests)
        # Pass gateway_id for slot reservation
        request_store = ActiveRequestStore()
        app.include_router(
            create_inference_router(
                self._config,
                request_store=request_store,
                gateway_socket_path=gateway_socket,
                gateway_url=gateway_url,
                gateway_id=gateway_id,
            )
        )

        # Cancel endpoint (shares request_store for parity with Remote)
        app.include_router(create_cancel_router(request_store))

        # VRAM measurement endpoint (proxied to Master via federation WS)
        from ..remote.api.measurement import create_measurement_router

        app.include_router(create_measurement_router(self._edge_server))

        # Gateway management proxy (for measure.py and admin operations)
        from ..remote.api.gateway_proxy import create_gateway_proxy_router

        gateway_proxy_router = create_gateway_proxy_router(
            gateway_socket_path=gateway_socket,
            gateway_url=gateway_url,
        )
        app.include_router(gateway_proxy_router)

        logger.info(
            "Edge mode: Gateway proxy endpoints mounted "
            "(jobs, status, models management)"
        )

        logger.info(
            "Edge mode: HTTP federation endpoints mounted "
            "(inference, tokens, models, cancel, gateway-proxy)"
        )

    def wire_gateway_telemetry(
        self, ws_client: "GatewayWebSocketClient", gateway_url: str
    ) -> None:
        """
        Wire local Gateway WebSocket callbacks to RemoteTelemetrySender.

        CRITICAL: This enables telemetry flow from local Gateway → Master.
        Must be called after both gateway and federation are initialized.

        Args:
            ws_client: Local Gateway's WebSocket client
            gateway_url: URL of the local gateway (for telemetry payload)
        """
        if self._config.mode != StargateMode.REMOTE:
            logger.debug("Skipping gateway telemetry wiring (not in REMOTE mode)")
            return

        if not self._mode_integration:
            logger.warning("Cannot wire telemetry: Remote mode not initialized")
            return

        if hasattr(self._mode_integration, "wire_gateway_telemetry"):
            self._mode_integration.wire_gateway_telemetry(ws_client, gateway_url)

    def wire_edge_gateway_telemetry(
        self, ws_client: "GatewayWebSocketClient", gateway_url: str
    ) -> None:
        """
        Wire local Gateway WebSocket callbacks to Edge telemetry forwarding.

        CRITICAL: Enables telemetry flow from local Gateway → Edge → Master.
        Must be called after both gateway and federation are initialized.

        For Edge mode only. Remote mode uses wire_gateway_telemetry() instead.

        Args:
            ws_client: Local Gateway's WebSocket client
            gateway_url: URL of the local gateway (for telemetry payload)
        """
        if self._config.mode != StargateMode.EDGE:
            logger.debug("Skipping Edge gateway telemetry wiring (not in EDGE mode)")
            return

        if not self._edge_server:
            logger.warning(
                "Cannot wire Edge telemetry: EdgeFederationServer not initialized"
            )
            return

        self._edge_server.wire_gateway_telemetry(ws_client, gateway_url)
