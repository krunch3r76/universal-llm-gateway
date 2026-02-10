"""
Remote WebSocket client for federation.

Connects TO Master Stargate (Remote-initiates model).

CRITICAL: Bounded backoff (30s max) - fixes original unbounded retry problem.

INVARIANT:
  max_reconnect_delay ≤ 30s
  ∧ outbound via bounded_queue.try_put()
  ∧ sustained_overflow ⟹ disconnect ∧ schedule_reconnect
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger
from universal_protocol.messages import MessageEnvelope
from universal_protocol.ws.bounded_queue import BoundedQueue
from websockets.client import WebSocketClientProtocol

from ....common.config import FederationConfig, StargateMode
from ....common.peer_connection import PeerConnection
from .auth import RemoteAuthClient
from .connection import connection_loop
from .message_io import ping_loop, receive_loop, send_pong, sender_loop

if TYPE_CHECKING:
    from ....master.telemetry.sender import RemoteTelemetrySender

logger = get_logger(__name__)

# Sustained overflow threshold (consecutive failures before disconnect)
DEFAULT_OVERFLOW_THRESHOLD = 3


class RemoteWebSocketClient(PeerConnection):
    """
    WebSocket client connecting Remote TO Master Stargate.

    CRITICAL: Uses bounded backoff (30s max) to fix unbounded retry problem.

    Lifecycle:
    1. Connect to Master via WSS
    2. Send federation_auth (Remote initiates)
    3. Receive federation_auth_result
    4. If accepted: start telemetry flow + ping/pong
    5. On disconnect: reconnect with bounded backoff (30s max)
    """

    def __init__(
        self,
        config: FederationConfig,
        on_telemetry: (
            Callable[[str, str, dict[str, Any]], Awaitable[None]] | None
        ) = None,
        on_cancel: Callable[[str, str | None], Awaitable[bool]] | None = None,
        on_connected: Callable[[], Awaitable[None]] | None = None,
        on_disconnected: Callable[[], Awaitable[None]] | None = None,
    ):
        if config.mode != StargateMode.REMOTE:
            raise ValueError("RemoteWebSocketClient requires REMOTE mode")
        if not config.master:
            raise ValueError("RemoteWebSocketClient requires 'master' config")

        self._config = config
        self._on_telemetry = on_telemetry
        self._on_cancel = on_cancel
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

        self._master_config = config.master

        self._auth_client = RemoteAuthClient(
            local_stargate_id=config.stargate_id,
            api_key=self._master_config.api_key,
            auth_timeout_seconds=5.0,
        )

        # Telemetry sender (initialized when connected)
        self._telemetry_sender: RemoteTelemetrySender | None = None
        # EdgeTelemetrySender for Edge Stargates
        self._edge_telemetry_sender: Any | None = None

        self._websocket: WebSocketClientProtocol | None = None
        self._authenticated = False
        self._running = False

        # CRITICAL: Bounded backoff - 30s max (FED-11)
        self._initial_delay = config.reconnect_interval_ms / 1000
        self._max_delay = min(
            config.max_reconnect_delay_ms / 1000,
            30.0,  # HARD CAP: 30 seconds maximum
        )
        self._jitter_factor = 0.1  # ±10% jitter

        # Bounded send queue
        self._send_queue = BoundedQueue(
            max_size=100,
            queue_id=f"remote-{config.stargate_id}",
        )
        self._sender_task: asyncio.Task[None] | None = None
        self._consecutive_overflows = 0
        self._overflow_threshold = DEFAULT_OVERFLOW_THRESHOLD

        # Ping task
        self._ping_task: asyncio.Task[None] | None = None
        self._ping_interval = config.ping_interval_ms / 1000

        # Connection task
        self._connect_task: asyncio.Task[None] | None = None

    # PeerConnection interface

    @property
    def peer_id(self) -> str:
        """Master's stargate_id."""
        return self._master_config.stargate_id

    @property
    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        return self._websocket is not None and self._authenticated

    async def send_telemetry(self, msg: MessageEnvelope) -> bool:
        """Send telemetry message to Master via bounded queue."""
        if not self._running or not self.is_connected:
            return False

        message = msg.to_dict()
        success = self._send_queue.try_put(message)

        if success:
            self._consecutive_overflows = 0
        else:
            self._consecutive_overflows += 1
            logger.warning(
                f"Queue overflow "
                f"({self._consecutive_overflows}/{self._overflow_threshold})"
            )

            if self._consecutive_overflows >= self._overflow_threshold:
                logger.error("Sustained queue overflow, triggering reconnect")
                asyncio.create_task(self._trigger_reconnect())

        return success

    async def send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send request to Master (future RPC)."""
        raise NotImplementedError("Request forwarding not implemented")

    # Client lifecycle

    async def connect(self) -> None:
        """Start connection with auto-reconnect."""
        self._running = True

        # Create telemetry sender
        from ....remote.telemetry.sender import RemoteTelemetrySender

        self._telemetry_sender = RemoteTelemetrySender(
            self._config,
            send_callback=self._send_telemetry_message,
        )
        self._telemetry_sender.start()

        self._connect_task = asyncio.create_task(
            connection_loop(
                master_config=self._master_config,
                auth_client=self._auth_client,
                stargate_id=self._config.stargate_id,
                initial_delay=self._initial_delay,
                max_delay=self._max_delay,
                jitter_factor=self._jitter_factor,
                is_running=lambda: self._running,
                set_running=lambda v: setattr(self, "_running", v),
                on_connect_success=self._on_authenticated,
                on_disconnect=self._on_session_end,
            ),
            name=f"remote-ws-{self._config.stargate_id}",
        )

    async def disconnect(self) -> None:
        """Stop connection and cleanup."""
        self._running = False

        # Stop telemetry sender
        if self._telemetry_sender:
            await self._telemetry_sender.stop()
            self._telemetry_sender = None

        # Cancel tasks
        for task in [self._connect_task, self._sender_task, self._ping_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._connect_task = None
        self._sender_task = None
        self._ping_task = None

        # Close WebSocket
        if self._websocket:
            await self._websocket.close()
            self._websocket = None

        self._authenticated = False

    # Internal callbacks

    async def _on_authenticated(self, ws: WebSocketClientProtocol) -> None:
        """Called when authenticated with Master."""
        self._websocket = ws
        self._authenticated = True

        # Signal telemetry sender that upstream is ready
        if self._telemetry_sender:
            self._telemetry_sender.signal_master_ready()

        # For Edge Stargates using EdgeTelemetrySender
        if self._edge_telemetry_sender:
            self._edge_telemetry_sender.signal_ready()

        # Start sender task
        self._sender_task = asyncio.create_task(
            sender_loop(
                websocket=ws,
                send_queue=self._send_queue,
                is_running=lambda: self._running,
                stargate_id=self._config.stargate_id,
            ),
            name=f"remote-sender-{self._config.stargate_id}",
        )

        if self._on_connected:
            await self._on_connected()

        # Start ping task
        self._ping_task = asyncio.create_task(
            ping_loop(
                send_queue=self._send_queue,
                ping_interval=self._ping_interval,
                is_running=lambda: self._running,
                has_websocket=lambda: self._websocket is not None,
            ),
            name=f"remote-ping-{self._config.stargate_id}",
        )

        # Run receive loop (blocks until disconnect)
        await receive_loop(
            websocket=ws,
            peer_id=self.peer_id,
            on_telemetry=self._on_telemetry,
            on_cancel=self._on_cancel,
            send_pong=send_pong,
        )

    async def _on_session_end(self) -> None:
        """Called when session ends (before reconnect)."""
        self._authenticated = False

        # Signal telemetry senders that upstream is disconnected
        if self._telemetry_sender:
            self._telemetry_sender.signal_master_disconnected()

        if self._edge_telemetry_sender:
            self._edge_telemetry_sender.signal_disconnected()

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

    async def _send_telemetry_message(
        self,
        msg: MessageEnvelope,
    ) -> bool:
        """Callback for telemetry sender."""
        return await self.send_telemetry(msg)

    async def _trigger_reconnect(self) -> None:
        """Trigger reconnection due to sustained overflow."""
        self._consecutive_overflows = 0

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass

    # Telemetry API (for integration)

    @property
    def telemetry_sender(self) -> RemoteTelemetrySender | None:
        """Telemetry sender for Gateway event integration."""
        return self._telemetry_sender


# Alias for protocol compatibility
WSRemoteClient = RemoteWebSocketClient
