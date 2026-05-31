"""
Local Edge client for Unix socket federation.

Connects to a network-isolated Edge Stargate over Unix socket
using the same federation protocol as Remote→Master.

Pattern: Reuses RemoteWebSocketClient patterns with Unix socket transport.

INVARIANT: ∀ outbound via bounded_queue.try_put() (non-blocking)
INVARIANT: max_reconnect_delay ≤ 30s (bounded backoff)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, override

from universal_logging import get_logger
from universal_protocol.messages import MessageEnvelope
from universal_protocol.ws.bounded_queue import BoundedQueue

from ....common.config import LocalEdgeConfig
from ....common.peer_connection import PeerConnection
from .auth import LocalEdgeAuthClient
from .connection import local_connection_loop
from .message_io import local_ping_loop, local_receive_loop, local_sender_loop
from .recovery import LocalEdgeRecoveryCoordinator

if TYPE_CHECKING:
    from websockets.client import WebSocketClientProtocol

logger = get_logger(__name__)

# Bounded backoff constants (same as RemoteWebSocketClient)
DEFAULT_INITIAL_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0  # HARD CAP per FED-11
DEFAULT_JITTER_FACTOR = 0.1
DEFAULT_OVERFLOW_THRESHOLD = 3


class LocalEdgeClient(PeerConnection):
    """
    WebSocket client connecting Relay to local Edge Stargate over Unix socket.

    Uses federation protocol (not Gateway protocol).
    Forwards Edge telemetry to Master via RemoteTelemetrySender.

    INVARIANT: Follows same patterns as RemoteWebSocketClient:
      - Bounded backoff (30s max)
      - Non-blocking sends via BoundedQueue
      - Federation auth handshake
    """

    def __init__(
        self,
        config: LocalEdgeConfig,
        relay_stargate_id: str,
        on_telemetry: (
            Callable[[str, str, dict[str, Any]], Awaitable[None]] | None
        ) = None,
        on_connected: Callable[[], Awaitable[None]] | None = None,
        on_disconnected: Callable[[], Awaitable[None]] | None = None,
        on_measurement_request: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
        ping_interval: float = 20.0,
    ):
        self._config = config
        self._relay_stargate_id = relay_stargate_id
        self._on_telemetry = on_telemetry
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_measurement_request = on_measurement_request
        self._ping_interval = ping_interval

        # CRITICAL: Auth uses Relay's stargate_id (not Edge's)
        # Edge validates against allowed_peers keyed by Relay ID
        self._auth_client = LocalEdgeAuthClient(
            local_stargate_id=relay_stargate_id,
            api_key=config.api_key,
            auth_timeout_seconds=5.0,
        )
        self._recovery = LocalEdgeRecoveryCoordinator(
            relay_stargate_id=relay_stargate_id,
            peer_id=config.stargate_id,
            socket_path=config.socket_path,
        )

        self._websocket: WebSocketClientProtocol | None = None
        self._authenticated = False
        self._running = False

        # Bounded backoff (same as RemoteWebSocketClient)
        self._initial_delay = DEFAULT_INITIAL_DELAY
        self._max_delay = DEFAULT_MAX_DELAY
        self._jitter_factor = DEFAULT_JITTER_FACTOR

        # Bounded send queue (CRITICAL: non-blocking sends)
        self._send_queue = BoundedQueue(
            max_size=100,
            queue_id=f"local-edge-{config.stargate_id}",
        )
        self._sender_task: asyncio.Task[None] | None = None
        self._consecutive_overflows = 0
        self._overflow_threshold = DEFAULT_OVERFLOW_THRESHOLD

        # Background tasks
        self._ping_task: asyncio.Task[None] | None = None
        self._connection_task: asyncio.Task[None] | None = None

    # PeerConnection interface

    @property
    @override
    def peer_id(self) -> str:
        """Edge's stargate_id."""
        return self._config.stargate_id

    @property
    @override
    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        return self._websocket is not None and self._authenticated

    @override
    async def send_telemetry(self, msg: MessageEnvelope) -> bool:
        """Send telemetry message to Edge via bounded queue (non-blocking)."""
        if not self._running or not self.is_connected:
            return False

        message = msg.to_dict()
        success = self._send_queue.try_put(message)

        if success:
            self._consecutive_overflows = 0
        else:
            self._consecutive_overflows += 1
            overflow_count = self._consecutive_overflows
            threshold = self._overflow_threshold
            logger.warning(f"Queue overflow ({overflow_count}/{threshold})")

            if self._consecutive_overflows >= self._overflow_threshold:
                logger.error("Sustained queue overflow, triggering reconnect")
                asyncio.create_task(self._trigger_reconnect())

        return success

    @override
    async def send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send request to Edge (future RPC)."""
        raise NotImplementedError("Request forwarding not implemented")

    # Client lifecycle

    @override
    async def connect(self) -> None:
        """Start connection with auto-reconnect."""
        if self._running:
            return

        self._running = True
        self._connection_task = asyncio.create_task(
            local_connection_loop(
                config=self._config,
                auth_client=self._auth_client,
                initial_delay=self._initial_delay,
                max_delay=self._max_delay,
                jitter_factor=self._jitter_factor,
                is_running=lambda: self._running,
                on_connect_success=self._on_authenticated,
                on_disconnect=self._on_session_end,
                recovery=self._recovery,
            ),
            name=f"local-edge-{self._config.stargate_id}",
        )

    @override
    async def disconnect(self) -> None:
        """Stop connection and cleanup."""
        self._running = False

        # Cancel tasks
        for task in [self._connection_task, self._sender_task, self._ping_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._connection_task = None
        self._sender_task = None
        self._ping_task = None

        # Close WebSocket
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

        self._authenticated = False
        await self._recovery.shutdown()

    # Internal callbacks

    async def _on_authenticated(self, ws: WebSocketClientProtocol) -> None:
        """Called when authenticated with Edge."""
        self._websocket = ws
        self._authenticated = True
        await self._recovery.note_connected()

        # Start sender task (non-blocking queue drain)
        self._sender_task = asyncio.create_task(
            local_sender_loop(
                websocket=ws,
                send_queue=self._send_queue,
                is_running=lambda: self._running,
                peer_id=self._config.stargate_id,
            ),
            name=f"local-edge-sender-{self._config.stargate_id}",
        )

        if self._on_connected:
            await self._on_connected()

        # Start ping task
        self._ping_task = asyncio.create_task(
            local_ping_loop(
                send_queue=self._send_queue,
                ping_interval=self._ping_interval,
                is_running=lambda: self._running,
                has_websocket=lambda: self._websocket is not None,
            ),
            name=f"local-edge-ping-{self._config.stargate_id}",
        )

        await local_receive_loop(
            websocket=ws,
            peer_id=self.peer_id,
            on_telemetry=self._on_telemetry,
            on_measurement_request=self._on_measurement_request,
        )

    async def _on_session_end(self) -> None:
        """Called when session ends (before reconnect)."""
        self._authenticated = False

        # Stop sender/ping tasks
        for task in [self._sender_task, self._ping_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._sender_task = None
        self._ping_task = None

        if self._on_disconnected:
            await self._on_disconnected()

    async def _trigger_reconnect(self) -> None:
        """Trigger reconnection due to sustained overflow."""
        self._consecutive_overflows = 0

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
