"""
Master mode setup.

Master Stargate accepts connections FROM Remotes and orchestrates routing.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI
from universal_logging import get_logger

from ..common.connection_manager import ConnectionManager
from ..link.http_polling.master.poller import HTTPPollingReceiver
from .circuit_breaker import FederationCircuitBreaker
from .manager.federated_gateway_manager import FederatedGatewayManager
from .routing.forward import FederatedRequestForwarder

if TYPE_CHECKING:
    from ..common.config import FederationConfig
    from ..common.config.schema import LocalEdgeConfig
    from ..common.health.status import FederationHealthHandler
    from ..link.ws.local.client import LocalEdgeClient
    from .edge_ws_clients import MasterEdgeWSClients
    from .orchestration.load_orchestrator import FederatedLoadOrchestrator
    from .orchestration.metrics import OrchestrationMetrics
    from .routing.orchestrator import MasterRequestTracker
    from .telemetry.receiver import MasterTelemetryReceiver

logger = get_logger(__name__)


class MasterIntegration:
    """
    Encapsulates Master mode setup.

    Manages:
    - Federated gateway manager
    - Connection manager (accepts FROM Remotes)
    - HTTP telemetry poller
    - Request forwarder
    - Load orchestrator
    """

    def __init__(
        self,
        config: "FederationConfig",
        event_bus: object | None = None,
        stargate_config: object | None = None,
        health_observer: Callable[..., None] | None = None,
    ) -> None:
        self._config = config
        self._event_bus = event_bus
        self._stargate_config = stargate_config
        self._health_observer = health_observer
        self._federated_manager: FederatedGatewayManager | None = None
        self._connection_manager: ConnectionManager | None = None
        self._http_pollers: dict[str, HTTPPollingReceiver] = {}
        self._edge_ws_clients: MasterEdgeWSClients | None = None
        self._forwarder: FederatedRequestForwarder | None = None
        self._circuit_breaker: FederationCircuitBreaker | None = None
        self._load_orchestrator: FederatedLoadOrchestrator | None = None
        self._orchestration_metrics: OrchestrationMetrics | None = None
        self._local_edge_client: LocalEdgeClient | None = None
        # Initialized in _create_federated_manager; routes local-edge telemetry
        # through MasterTelemetryReceiver so REQUEST_INFERENCE_STARTED reaches the bus
        self._telemetry_receiver: MasterTelemetryReceiver | None = None
        # Set in _setup_master_forwarding_and_orchestration
        self._request_tracker: MasterRequestTracker | None = None
        # Cloud backend integration
        self._cloud_registry: object | None = None
        self._cloud_forwarder: object | None = None

    async def setup(
        self,
        app: FastAPI,
        gateway_socket_path: str | None = None,
        on_peer_connected: Callable[[str], Awaitable[None]] | None = None,
        on_peer_disconnected: Callable[[str], Awaitable[None]] | None = None,
    ) -> "FederationHealthHandler":
        """
        Setup Master mode.

        Args:
            app: FastAPI application
            gateway_socket_path: Optional gateway socket path for token counting
            on_peer_connected: Callback for peer connection
            on_peer_disconnected: Callback for peer disconnection

        Returns:
            Initialized FederationHealthHandler
        """
        from ..common.health.status import FederationHealthHandler

        self._register_master_routers(app, gateway_socket_path)
        self._federated_manager = await self._create_federated_manager()
        self._register_remotes()
        await self._start_local_edge_client()
        self._circuit_breaker = self._create_circuit_breaker()
        await self._start_master_connections(
            app, on_peer_connected, on_peer_disconnected
        )
        await self._start_master_initiated_edge_ws_clients()
        await self._start_http_telemetry_poller_if_needed()
        await self._start_cloud_providers()
        self._setup_master_forwarding_and_orchestration(app)

        # Validate tracker was created
        if self._request_tracker is None:
            logger.error(
                "❌ Master mode setup completed but request_tracker is None. "
                "Compute-type limits will NOT be enforced."
            )

        health_handler = FederationHealthHandler(
            self._config,
            federated_manager=self._federated_manager,
        )
        logger.info("✅ Master mode setup complete")
        return health_handler

    @property
    def federated_manager(self) -> FederatedGatewayManager | None:
        return self._federated_manager

    @property
    def connection_manager(self) -> ConnectionManager | None:
        return self._connection_manager

    @property
    def http_pollers(self) -> dict[str, HTTPPollingReceiver]:
        return self._http_pollers

    @property
    def forwarder(self) -> FederatedRequestForwarder | None:
        return self._forwarder

    @property
    def circuit_breaker(self) -> FederationCircuitBreaker | None:
        return self._circuit_breaker

    @property
    def load_orchestrator(self) -> "FederatedLoadOrchestrator | None":
        return self._load_orchestrator

    @property
    def request_tracker(self) -> "MasterRequestTracker | None":
        """
        Get MasterRequestTracker for routing feasibility checks.

        Returns:
            MasterRequestTracker if configured (Master mode), None otherwise

        Note:
            None is valid for Edge/Remote modes. Master mode should always
            have tracker after setup() completes.
        """
        return self._request_tracker

    def _register_master_routers(
        self, app: FastAPI, gateway_socket_path: str | None
    ) -> None:
        """Register HTTP routers for Master mode.

        Master does not register a token-counting router; token counting is
        delegated to the execution target (Remote Stargate).
        """
        pass

    async def _create_federated_manager(self) -> FederatedGatewayManager:
        """
        Create and start federated gateway manager.

        Returns:
            Started FederatedGatewayManager instance

        Note:
            CRITICAL: Executor must start before telemetry can flow
        """
        if self._event_bus is None:
            raise RuntimeError("EventBus required for FederatedGatewayManager")
        manager = FederatedGatewayManager(event_bus=self._event_bus)
        await manager.start()

        from .telemetry.receiver import MasterTelemetryReceiver

        self._telemetry_receiver = MasterTelemetryReceiver(
            on_telemetry=manager.update_from_event,
            event_bus=self._event_bus,
        )
        return manager

    def _register_remotes(self) -> None:
        """
        Register remotes from config with federated manager.

        Note:
            Config-only registration. Gateway creation happens in update_from_event.
        """
        assert self._federated_manager is not None, "Manager must be created first"
        for remote in self._config.remotes:
            self._federated_manager.register_remote(
                remote.stargate_id,
                remote.url,
                is_http_polling=remote.disable_websocket,
                config=remote,
            )

    async def _start_local_edge_client(self) -> None:
        """Connect to local Edge Stargate over Unix socket."""
        if not self._config.local_edge:
            return

        assert self._federated_manager is not None, "Manager must be created first"

        edge_config = self._config.local_edge
        self._register_local_edge_remote(edge_config)
        self._local_edge_client = self._create_local_edge_client(edge_config)
        await self._connect_local_edge_client(self._local_edge_client, edge_config)

    def _register_local_edge_remote(self, edge_config: "LocalEdgeConfig") -> None:
        """Register local Edge with federated manager for gateway creation."""
        assert self._federated_manager is not None

        self._federated_manager.register_remote(
            edge_config.stargate_id,
            f"unix://{edge_config.socket_path}",
            is_http_polling=False,
            config=edge_config,
        )

    def _create_local_edge_client(
        self, edge_config: "LocalEdgeConfig"
    ) -> "LocalEdgeClient":
        """Create LocalEdgeClient with callbacks wired."""
        from ..link.ws.local import LocalEdgeClient

        return LocalEdgeClient(
            config=edge_config,
            relay_stargate_id=self._config.stargate_id,
            on_telemetry=self._process_edge_telemetry,
            on_connected=self._on_edge_connected,
            on_disconnected=self._on_edge_disconnected,
            on_measurement_request=self._handle_vram_measurement,
        )

    async def _connect_local_edge_client(
        self, client: "LocalEdgeClient", edge_config: "LocalEdgeConfig"
    ) -> None:
        """Connect to Edge and log success."""
        await client.connect()
        logger.info(f"Started LocalEdgeClient for {edge_config.stargate_id}")

    async def _process_edge_telemetry(
        self, peer_id: str, msg_type: str, data: dict[str, object]
    ) -> None:
        """Process Edge telemetry locally (Master is final destination)."""
        if not self._federated_manager:
            return
        assert self._telemetry_receiver is not None
        await self._telemetry_receiver.handle_message(peer_id, msg_type, data)

    async def _handle_vram_measurement(
        self, data: dict[str, object]
    ) -> dict[str, object]:
        """Handle VRAM measurement request from Edge (runs pynvml on host)."""
        from .measurement.vram import measure_gpu_vram

        device_index = int(data.get("device_index", 0))
        snapshot = measure_gpu_vram(device_index)
        if snapshot is None:
            return {"total_mb": None, "process_count": None}
        return {"total_mb": snapshot.total_mb, "process_count": snapshot.process_count}

    async def _on_edge_connected(self) -> None:
        """Handle Edge connection."""
        logger.info("✅ Connected to local Edge Stargate via federation")

    async def _on_edge_disconnected(self) -> None:
        """Handle Edge disconnection."""
        logger.warning("⚠️ Disconnected from local Edge Stargate")
        # Optionally remove gateways from this Edge
        if self._federated_manager and self._config.local_edge:
            await self._federated_manager.remove_remote_gateways(
                self._config.local_edge.stargate_id
            )

    def _create_circuit_breaker(self) -> FederationCircuitBreaker:
        """
        Create circuit breaker with explicit configuration.

        Expects self._config.circuit_breaker to be fully populated.
        Fail-fast: missing cb_config raises AttributeError.
        """
        cb_config = self._config.circuit_breaker
        return FederationCircuitBreaker(
            failure_threshold=cb_config.failure_threshold,
            recovery_timeout_seconds=cb_config.recovery_timeout_seconds,
            half_open_max_requests=cb_config.half_open_max_requests,
            event_bus=self._event_bus,
        )

    async def _start_master_connections(
        self,
        app: FastAPI,
        on_peer_connected: Callable[[str], Awaitable[None]] | None = None,
        on_peer_disconnected: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """
        Start Master connection manager and register WebSocket router.

        Args:
            app: FastAPI application for router registration
            on_peer_connected: Optional callback for peer connection
            on_peer_disconnected: Optional callback for peer disconnection

        Note:
            Connection manager accepts connections FROM Remote Stargates.
            Callbacks are optional (Master doesn't need to track peer events).
        """
        assert self._federated_manager is not None, "Manager must be created first"
        from .peer_callbacks import build_peer_callbacks

        connected_cb, disconnected_cb = build_peer_callbacks(
            event_bus=self._event_bus,
            on_peer_connected=on_peer_connected,
            on_peer_disconnected=on_peer_disconnected,
        )

        self._connection_manager = ConnectionManager(
            self._config,
            on_telemetry=self._federated_manager.update_from_event,
            on_peer_connected=connected_cb,
            on_peer_disconnected=disconnected_cb,
            event_bus=self._event_bus,
        )
        await self._connection_manager.start()

        # Add Master WebSocket router
        if server := self._connection_manager.master_server:
            from ..link.ws.master.endpoint import create_master_ws_router

            router = create_master_ws_router(server, event_bus=self._event_bus)
            app.include_router(router)

    async def _start_http_telemetry_poller_if_needed(self) -> None:
        """
        Start HTTP telemetry poller(s) for remotes that use HTTP polling.

        Note:
            Creates one poller per remote with disable_websocket=True
        """
        assert self._federated_manager is not None, "Manager must be created first"
        http_remotes = [r for r in self._config.remotes if r.disable_websocket]

        if not http_remotes:
            return

        if not self._event_bus:
            logger.warning(
                "⚠️ HTTP telemetry poller requires event_bus for critical events, "
                "but event_bus not provided. Poller will not start."
            )
            return

        for remote in http_remotes:
            poller = HTTPPollingReceiver(
                remote_config=remote,
                config=self._config,
                gateway_manager=self._federated_manager,
                event_bus=self._event_bus,
            )
            self._http_pollers[remote.stargate_id] = poller
            await poller.start()
            logger.info(
                f"Registered relay stargate {remote.stargate_id} "
                f"(telemetry={remote.telemetry_transport})"
            )

    def _setup_master_forwarding_and_orchestration(self, app: FastAPI) -> None:
        """
        Setup request forwarding and load orchestration for Master.

        Args:
            app: FastAPI application for metrics endpoint registration
        """
        from .orchestration_wiring import wire_orchestration

        components = wire_orchestration(
            app=app,
            config=self._config,
            federated_manager=self._federated_manager,
            connection_manager=self._connection_manager,
            event_bus=self._event_bus,
            cloud_forwarder=self._cloud_forwarder,
        )
        self._forwarder = components.forwarder
        self._load_orchestrator = components.load_orchestrator
        self._orchestration_metrics = components.metrics
        self._request_tracker = components.request_tracker

    async def _start_master_initiated_edge_ws_clients(self) -> None:
        """Start Master→Edge WS telemetry clients for eligible remotes."""
        assert self._federated_manager is not None, "Manager must be created first"
        from .edge_ws_clients import MasterEdgeWSClients

        self._edge_ws_clients = MasterEdgeWSClients(
            config=self._config,
            on_telemetry=self._process_edge_telemetry,
            event_bus=self._event_bus,
        )
        await self._edge_ws_clients.start()

    async def _start_cloud_providers(self) -> None:
        """Connect to cloud proxy if configured."""
        if not self._stargate_config:
            return

        from systems.cloud.config import parse_cloud_proxy_config
        from systems.cloud.forwarder import CloudProxyClient
        from systems.cloud.registry import CloudProxyCatalogPoller

        raw_config = self._stargate_config.get_cloud_proxy_config()
        proxy_config = parse_cloud_proxy_config(raw_config)
        if not proxy_config:
            return

        self._cloud_forwarder = CloudProxyClient(
            proxy_config.url,
            health_observer=self._health_observer,
        )

        self._cloud_registry = CloudProxyCatalogPoller(
            proxy_config=proxy_config,
            gateway_manager=self._federated_manager,
            event_bus=self._event_bus,
        )
        await self._cloud_registry.startup()

        logger.info("Cloud proxy client initialized: %s", proxy_config.url)

    async def shutdown(self) -> None:
        """Shutdown Master mode components.

        _cloud_registry is shut down directly; _cloud_forwarder (client) is
        closed via forwarder.close().
        """
        if self._cloud_registry:
            await self._cloud_registry.shutdown()
            self._cloud_registry = None

        # Stop local edge client
        if self._local_edge_client:
            await self._local_edge_client.disconnect()
            self._local_edge_client = None

        if self._edge_ws_clients:
            await self._edge_ws_clients.stop()
            self._edge_ws_clients = None

        for poller in self._http_pollers.values():
            await poller.stop()
        self._http_pollers.clear()

        if self._connection_manager:
            await self._connection_manager.stop()

        if self._federated_manager:
            await self._federated_manager.stop()

        if self._forwarder:
            await self._forwarder.close()
