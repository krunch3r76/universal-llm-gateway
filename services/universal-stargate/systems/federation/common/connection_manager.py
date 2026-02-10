"""
Connection manager abstraction for federation.

Manages lifecycle of both client and server connections based on mode.

NOTE: This is the NEW unified ConnectionManager for Remote→Master direction.
The OLD ws_client/connection_manager.py (Master→Remote) will be deleted in Phase 4.

INVARIANT:
  mode = MASTER ⟹ ws_server active ∧ ws_client = None
  mode = REMOTE ⟹ ws_client active ∧ ws_server = None
  mode = PEER ⟹ ws_server active ∧ ws_client active (future)

Phase 2/3 Implementation Contracts:
    _ws_server (Phase 2 - MasterWebSocketServer) must provide:
        - connected_peers: list[str] property
        - get_connection(peer_id: str) -> PeerConnection | None
        - start() -> Awaitable[None]
        - stop() -> Awaitable[None]

    _ws_client (Phase 3 - RemoteWebSocketClient) must provide:
        - peer_id: str property
        - is_connected: bool property
        - connect() -> Awaitable[None]
        - disconnect() -> Awaitable[None]
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .config import FederationConfig, StargateMode
from .peer_connection import PeerConnection

if TYPE_CHECKING:
    from ..link.ws.master.server import MasterWebSocketServer

logger = get_logger(__name__)


class ConnectionManager:
    """
    Manages federation connection lifecycle.

    In current implementation:
    - MASTER mode: Creates WebSocket server (accepts Remote connections) - Phase 2
    - REMOTE mode: Creates WebSocket client (connects TO Master) - Phase 3
    - EDGE mode: No connections
    - PEER mode: Both (future)
    """

    def __init__(
        self,
        config: FederationConfig,
        on_telemetry: (
            Callable[[str, str, dict[str, Any]], Awaitable[None]] | None
        ) = None,
        on_cancel: Callable[[str, str | None], Awaitable[bool]] | None = None,
        on_peer_connected: Callable[[str], Awaitable[None]] | None = None,
        on_peer_disconnected: Callable[[str], Awaitable[None]] | None = None,
        event_bus: Any | None = None,
    ):
        """
        Initialize connection manager.

        Args:
            config: Federation configuration
            on_telemetry: Callback(peer_id, signal, payload) for received telemetry
                         (required for MASTER mode, optional for REMOTE)
            on_cancel: Callback(request_id, model_id) for cancel messages
                      (REMOTE mode: handles cancel requests from Master)
            on_peer_connected: Callback(peer_id) when peer connects
            on_peer_disconnected: Callback(peer_id) when peer disconnects
            event_bus: Optional event bus for connection events
        """
        self._config = config
        self._on_telemetry = on_telemetry
        self._on_cancel = on_cancel
        self._on_peer_connected = on_peer_connected
        self._on_peer_disconnected = on_peer_disconnected
        self._event_bus = event_bus

        # Implementations set in Phase 2/3
        self._ws_server: MasterWebSocketServer | None = None  # Phase 2
        self._ws_client: PeerConnection | None = None  # Phase 3

        self._started = False

    @property
    def mode(self) -> StargateMode:
        """Current federation mode."""
        return self._config.mode

    @property
    def connected_peers(self) -> list[str]:
        """List of connected peer stargate_ids."""
        peers: list[str] = []
        if self._ws_server:
            peers.extend(self._ws_server.connected_peers)
        if self._ws_client and self._ws_client.is_connected:
            peers.append(self._ws_client.peer_id)
        return peers

    def get_connection(self, peer_id: str) -> PeerConnection | None:
        """Get connection for a specific peer."""
        if self._ws_server:
            if conn := self._ws_server.get_connection(peer_id):
                return conn
        if self._ws_client and self._ws_client.peer_id == peer_id:
            return self._ws_client
        return None

    async def start(self) -> None:
        """
        Start connections based on mode.

        Phase 1: Logs mode, no actual connections (stub)
        Phase 2: Master mode creates MasterWebSocketServer
        Phase 3: Remote mode creates RemoteWebSocketClient
        """
        if self._started:
            return

        logger.info(f"Starting ConnectionManager (mode={self._config.mode.value})")

        if self._config.mode == StargateMode.MASTER:
            if not self._on_telemetry:
                raise ValueError(
                    "on_telemetry callback required for MASTER mode "
                    "(Master receives telemetry from Remotes)"
                )

            from ..link.ws.master.server import MasterWebSocketServer

            self._ws_server = MasterWebSocketServer(
                self._config,
                on_telemetry=self._on_telemetry,
                on_peer_connected=self._on_peer_connected,
                on_peer_disconnected=self._on_peer_disconnected,
                event_bus=self._event_bus,
            )
            await self._ws_server.start()

        elif self._config.mode == StargateMode.REMOTE:
            # NEW: Skip WebSocket if disabled (Golem compatibility)
            if self._config.disable_websocket:
                logger.info(
                    "WebSocket disabled (Golem mode) - telemetry via HTTP polling only"
                )
            else:
                from ..link.ws.remote.client import RemoteWebSocketClient

                self._ws_client = RemoteWebSocketClient(
                    self._config,
                    on_telemetry=self._on_telemetry,
                    on_cancel=self._on_cancel,
                    on_connected=self._handle_connected,
                    on_disconnected=self._handle_disconnected,
                )
                await self._ws_client.connect()

        elif self._config.mode == StargateMode.EDGE:
            logger.info("Edge mode: no federation connections")

        self._started = True

    async def stop(self) -> None:
        """Stop all connections."""
        if not self._started:
            return

        logger.info("Stopping ConnectionManager")

        if self._ws_server:
            await self._ws_server.stop()
            self._ws_server = None

        if self._ws_client:
            await self._ws_client.disconnect()
            self._ws_client = None

        self._started = False

    # Internal handlers for Phase 2/3

    async def _handle_connected(self) -> None:
        """Handle connection to Master (Remote mode)."""
        if self._on_peer_connected and self._ws_client:
            await self._on_peer_connected(self._ws_client.peer_id)

    async def _handle_disconnected(self) -> None:
        """Handle disconnection from Master (Remote mode)."""
        if self._on_peer_disconnected and self._ws_client:
            await self._on_peer_disconnected(self._ws_client.peer_id)

    @property
    def master_server(self) -> "MasterWebSocketServer | None":
        """Master WebSocket server (if in MASTER mode)."""
        return self._ws_server

    @property
    def remote_client(self) -> PeerConnection | None:
        """
        Get Remote WebSocket client (if in REMOTE mode).

        Returns:
            RemoteWebSocketClient instance if in REMOTE mode and connected,
            None otherwise.
        """
        if self._config.mode == StargateMode.REMOTE:
            return self._ws_client
        return None

    async def send_cancel(
        self,
        remote_stargate_id: str,
        request_id: str,
        model_id: str | None = None,
    ) -> bool:
        """
        Send cancellation request to a remote stargate via WebSocket.

        Args:
            remote_stargate_id: The stargate ID of the remote to cancel on
            request_id: The request ID of the request to cancel
            model_id: Optional model ID for queue-specific cancellation

        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self._ws_server:
            logger.warning(
                f"Cannot send cancel to {remote_stargate_id}: no WebSocket server"
            )
            return False

        # Get the authenticated remote connection
        remote = self._ws_server.auth_handler.get_connection(remote_stargate_id)
        if not remote:
            logger.warning(f"Cannot send cancel to {remote_stargate_id}: not connected")
            return False

        try:
            from .protocol import create_request_cancel

            msg = create_request_cancel(request_id, model_id)
            await remote.ws.send_json(msg.to_dict())
            logger.info(f"Sent cancel to {remote_stargate_id} for {request_id[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to send cancel to {remote_stargate_id}: {e}")
            return False
