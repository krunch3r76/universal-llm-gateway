"""
Master WebSocket server for federation.

Accepts connections from Remote Stargates.

INVARIANT:
  mode = MASTER ⟹ ws_server active
  ∧ ∀ connection: authenticated within deadline
"""

from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from ....common.config import FederationConfig, StargateMode
from ....common.peer_connection import PeerConnection
from ....master.telemetry.receiver import MasterTelemetryReceiver
from .auth import AuthenticatedRemote, MasterAuthHandler

logger = get_logger(__name__)


class RemotePeerConnection(PeerConnection):
    """
    PeerConnection wrapper for a connected Remote.

    Adapts AuthenticatedRemote to PeerConnection protocol.
    """

    def __init__(self, remote: AuthenticatedRemote):
        self._remote = remote

    @property
    def peer_id(self) -> str:
        return self._remote.stargate_id

    @property
    def is_connected(self) -> bool:
        return True  # Only created for active connections

    async def send_telemetry(self, signal: str, payload: dict[str, Any]) -> bool:
        """Send telemetry TO Remote (for future bidirectional)."""
        # Master doesn't currently send telemetry to Remotes
        # This is here for PeerConnection interface compliance
        return False

    async def send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send request to Remote (future RPC)."""
        raise NotImplementedError("Request forwarding not implemented")

    async def disconnect(self) -> None:
        """
        Disconnect from Remote (no-op for server-side connections).

        Server-side connections are managed by MasterWebSocketServer.
        The server handles lifecycle (connect/disconnect) via its own
        stop() method and connection tracking.

        This method exists for PeerConnection protocol compliance.
        """
        pass  # Server manages connection lifecycle


class MasterWebSocketServer:
    """
    WebSocket server for Master mode.

    Accepts connections from Remotes, handles auth, receives telemetry.
    """

    def __init__(
        self,
        config: FederationConfig,
        on_telemetry: Callable[[str, str, dict[str, Any]], Awaitable[None]],
        on_peer_connected: Callable[[str], Awaitable[None]] | None = None,
        on_peer_disconnected: Callable[[str], Awaitable[None]] | None = None,
        event_bus: Any | None = None,
    ):
        if config.mode != StargateMode.MASTER:
            raise ValueError("MasterWebSocketServer requires MASTER mode")

        self._config = config
        self._on_peer_connected = on_peer_connected
        self._on_peer_disconnected = on_peer_disconnected

        self._auth_handler = MasterAuthHandler(config)
        self._telemetry_receiver = MasterTelemetryReceiver(
            on_telemetry=on_telemetry,
            event_bus=event_bus,
        )

        self._started = False

    @property
    def connected_peers(self) -> list[str]:
        """List of connected Remote stargate_ids."""
        return self._auth_handler.connected_remotes

    def get_connection(self, peer_id: str) -> PeerConnection | None:
        """Get PeerConnection for a specific Remote."""
        if remote := self._auth_handler.get_connection(peer_id):
            return RemotePeerConnection(remote)
        return None

    @property
    def auth_handler(self) -> MasterAuthHandler:
        """Auth handler (for endpoint integration)."""
        return self._auth_handler

    @property
    def telemetry_receiver(self) -> MasterTelemetryReceiver:
        """Telemetry receiver (for endpoint integration)."""
        return self._telemetry_receiver

    async def start(self) -> None:
        """Start the server (registers with app in integration)."""
        if self._started:
            return

        logger.info(
            f"🚀 Master WebSocket server ready on {self._config.ws_server.path}"
        )
        self._started = True

    async def stop(self) -> None:
        """Stop the server."""
        if not self._started:
            return

        logger.info("Stopping Master WebSocket server")
        self._started = False

    async def handle_peer_connected(self, remote_id: str) -> None:
        """Notify that a Remote connected."""
        if self._on_peer_connected:
            await self._on_peer_connected(remote_id)

    async def handle_peer_disconnected(self, remote_id: str) -> None:
        """Notify that a Remote disconnected."""
        self._auth_handler.remove_connection(remote_id)
        if self._on_peer_disconnected:
            await self._on_peer_disconnected(remote_id)


class WSMasterReceiver:
    """
    WebSocket telemetry receiver (implements TelemetryReceiver protocol).

    Wraps MasterWebSocketServer for consistent interface with
    HTTPPollingReceiver.

    INVARIANT: Uses gateway_manager.update_from_event as telemetry callback
               (same pattern as ConnectionManager in master/integration.py)
    """

    def __init__(
        self,
        config: FederationConfig,
        gateway_manager: Any,
        event_bus: Any,  # noqa: ARG002 - Reserved for future event publishing
    ):
        # Use gateway_manager.update_from_event directly
        # (same callback pattern as ConnectionManager wiring)
        self._server = MasterWebSocketServer(
            config=config,
            on_telemetry=gateway_manager.update_from_event,
        )

    async def start(self) -> None:
        """Start WebSocket server."""
        await self._server.start()

    async def stop(self) -> None:
        """Stop WebSocket server."""
        await self._server.stop()

    def is_running(self) -> bool:
        """Check if server is running."""
        return self._server._started

    # Expose underlying server for backwards compatibility
    @property
    def server(self) -> MasterWebSocketServer:
        """Access underlying WebSocket server."""
        return self._server
