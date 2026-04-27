"""
Master-initiated WebSocket client for Edge telemetry.

Connects to an Edge Stargate's /ws/federation/edge endpoint and receives telemetry.

This is used when Master can reach worker endpoints (e.g. via Golem port tunnels),
so we avoid HTTP polling telemetry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, override

from universal_logging import get_logger
from universal_protocol.messages import MessageEnvelope
from universal_protocol.ws.bounded_queue import BoundedQueue

from ....common.peer_connection import PeerConnection
from ..local.auth import LocalEdgeAuthClient
from ..local.message_io import local_ping_loop, local_receive_loop, local_sender_loop
from .connection import connection_loop

if TYPE_CHECKING:
    from websockets.client import WebSocketClientProtocol

    from ....common.config.schema import FederationConfig, RemoteStargateConfig

logger = get_logger(__name__)


class MasterEdgeWebSocketClient(PeerConnection):
    """
    WebSocket client connecting Master TO Edge for telemetry.

    - Auth handshake: FederationAuth → FederationAuthResult
    - Telemetry: Edge pushes telemetry.* messages (MessageEnvelope)
    - Keepalive: periodic FederationPing, handle pong/ping
    """

    def __init__(
        self,
        *,
        config: FederationConfig,
        remote_config: RemoteStargateConfig,
        on_telemetry: (
            Callable[[str, str, dict[str, Any]], Awaitable[None]] | None
        ) = None,
        on_connected: Callable[[str], Awaitable[None]] | None = None,
        on_disconnected: Callable[[str], Awaitable[None]] | None = None,
        ping_interval: float | None = None,
        event_bus: Any | None = None,
    ):
        self._config = config
        self._remote_config = remote_config
        self._on_telemetry = on_telemetry
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._event_bus = event_bus

        self._websocket: WebSocketClientProtocol | None = None
        self._authenticated = False
        self._running = False

        # Bounded backoff (FED-11: 30s cap)
        self._initial_delay = config.reconnect_interval_ms / 1000
        self._max_delay = min(config.max_reconnect_delay_ms / 1000, 30.0)
        self._jitter_factor = 0.1

        # Ping interval: reuse federation config default unless overridden
        self._ping_interval = ping_interval or (config.ping_interval_ms / 1000)
        # Native WS keepalive timeout (see remote/client.py for rationale)
        self._ws_ping_timeout = min(self._ping_interval / 2, 10.0)

        # Auth: Master identifies as its own stargate_id, key is per-remote
        self._auth_client = LocalEdgeAuthClient(
            local_stargate_id=config.stargate_id,
            api_key=remote_config.api_key,
            auth_timeout_seconds=5.0,
        )

        # Bounded send queue (non-blocking ping/control)
        self._send_queue = BoundedQueue(
            max_size=100,
            queue_id=f"master-edge-{remote_config.stargate_id}",
        )
        self._sender_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._connection_task: asyncio.Task[None] | None = None

    @property
    @override
    def peer_id(self) -> str:
        return self._remote_config.stargate_id

    @property
    @override
    def is_connected(self) -> bool:
        return self._websocket is not None and self._authenticated

    @override
    async def send_telemetry(self, msg: MessageEnvelope) -> bool:  # noqa: ARG002
        # Master does not send telemetry to Edge (receiver-only).
        return False

    @override
    async def send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Request forwarding not implemented via WebSocket")

    async def connect(self) -> None:
        """Start connection with auto-reconnect."""
        if self._running:
            return

        self._running = True
        self._connection_task = asyncio.create_task(
            connection_loop(
                remote_config=self._remote_config,
                auth_client=self._auth_client,
                initial_delay=self._initial_delay,
                max_delay=self._max_delay,
                jitter_factor=self._jitter_factor,
                is_running=lambda: self._running,
                on_connect_success=self._on_authenticated,
                on_disconnect=self._on_session_end,
                ws_ping_interval=self._ping_interval,
                ws_ping_timeout=self._ws_ping_timeout,
                event_bus=self._event_bus,
            ),
            name=f"master-edge-{self._remote_config.stargate_id}",
        )

    @override
    async def disconnect(self) -> None:
        """Stop connection and cleanup."""
        self._running = False

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

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

        self._authenticated = False

    async def _on_authenticated(self, ws: WebSocketClientProtocol) -> None:
        """Called when authenticated with Edge."""
        self._websocket = ws
        self._authenticated = True

        remote_id = self._remote_config.stargate_id
        if self._on_connected:
            await self._on_connected(remote_id)

        # Start sender loop (drains bounded queue)
        self._sender_task = asyncio.create_task(
            local_sender_loop(
                websocket=ws,
                send_queue=self._send_queue,
                is_running=lambda: self._running,
                peer_id=remote_id,
            ),
            name=f"master-edge-sender-{remote_id}",
        )

        # Start ping loop (enqueue pings)
        self._ping_task = asyncio.create_task(
            local_ping_loop(
                send_queue=self._send_queue,
                ping_interval=self._ping_interval,
                is_running=lambda: self._running,
                has_websocket=lambda: self._websocket is not None,
            ),
            name=f"master-edge-ping-{remote_id}",
        )

        # Receive loop blocks until disconnect
        await local_receive_loop(
            websocket=ws,
            peer_id=remote_id,
            on_telemetry=self._on_telemetry,
        )

    async def _on_session_end(self) -> None:
        """Called when session ends (before reconnect)."""
        self._authenticated = False
        remote_id = self._remote_config.stargate_id

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
            await self._on_disconnected(remote_id)
