"""
WebSocket connection lifecycle for Master-initiated Edge telemetry.

Master connects TO Edge's /ws/federation/edge endpoint to receive telemetry
in environments where Master can reach worker endpoints (e.g. Golem port tunnels).

CRITICAL: Bounded backoff (30s max) per FED-11 invariant.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import websockets
from universal_logging import get_logger
from websockets.client import WebSocketClientProtocol

if TYPE_CHECKING:
    from ....common.config.schema import RemoteStargateConfig
    from ..local.auth import LocalEdgeAuthClient

logger = get_logger(__name__)


def _build_edge_ws_url(remote_url: str) -> str:
    """
    Constructs the full WebSocket URL for Master-to-Edge federation.

    Args:
        remote_url: The base URL of the remote Edge stargate.

    Returns:
        The complete WebSocket URL for the federation endpoint.

    Raises:
        ValueError: If a unix:// URL is provided, as it's not supported for this client.
    """
    base = remote_url.rstrip("/")
    if base.startswith("unix://"):
        raise ValueError("Master→Edge WebSocket client does not support unix:// URLs")
    if base.startswith("ws://") or base.startswith("wss://"):
        ws_base = base
    else:
        ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base}/ws/federation/edge"


async def connection_loop(
    *,
    remote_config: RemoteStargateConfig,
    auth_client: LocalEdgeAuthClient,
    initial_delay: float,
    max_delay: float,
    jitter_factor: float,
    is_running: Callable[[], bool],
    on_connect_success: Callable[[WebSocketClientProtocol], Awaitable[None]],
    on_disconnect: Callable[[], Awaitable[None]],
    ws_ping_interval: float = 15.0,
    ws_ping_timeout: float | None = None,
) -> None:
    """
    Main connection loop with bounded backoff.

    Args:
        remote_config: Target Edge stargate config (url, stargate_id, api_key)
        auth_client: Auth handshake client (FederationAuth → FederationAuthResult)
        initial_delay: Initial reconnect delay (seconds)
        max_delay: Maximum reconnect delay (seconds, capped at 30s)
        jitter_factor: Delay jitter factor (e.g., 0.1 = ±10%)
        is_running: Callback to check if client should continue
        on_connect_success: Callback when authenticated (receives websocket)
        on_disconnect: Callback when disconnected
        ws_ping_timeout: Pong deadline. None → min(ws_ping_interval/2, 10.0).
    """
    if max_delay > 30.0:
        logger.warning(
            f"max_delay {max_delay} exceeds FED-11 invariant of 30s. Capping at 30s."
        )
        max_delay = 30.0

    current_delay = initial_delay
    remote_id = remote_config.stargate_id
    ws_url = _build_edge_ws_url(remote_config.url)

    logger.info(
        f"🔄 Master→Edge connection loop started (remote={remote_id}, ws_url={ws_url})"
    )

    while is_running():
        try:
            await connect_once(
                remote_config=remote_config,
                auth_client=auth_client,
                on_connect_success=on_connect_success,
                on_disconnect=on_disconnect,
                ws_ping_interval=ws_ping_interval,
                ws_ping_timeout=ws_ping_timeout,
            )
            # Reset delay on successful session
            current_delay = initial_delay
        except asyncio.CancelledError:
            logger.info("Master→Edge connection loop cancelled")
            break
        except Exception as e:
            logger.warning(f"Connection to Edge failed ({remote_id}): {e}")

        if is_running():
            jitter = current_delay * jitter_factor
            delay = current_delay + random.uniform(-jitter, jitter)
            logger.info(
                f"Reconnecting to Edge {remote_id} in {delay:.1f}s (max {max_delay}s)"
            )
            await asyncio.sleep(delay)
            current_delay = min(current_delay * 2, max_delay)


async def connect_once(
    *,
    remote_config: RemoteStargateConfig,
    auth_client: LocalEdgeAuthClient,
    on_connect_success: Callable[[WebSocketClientProtocol], Awaitable[None]],
    on_disconnect: Callable[[], Awaitable[None]],
    ws_ping_interval: float = 15.0,
    ws_ping_timeout: float | None = None,
) -> None:
    """
    Single connection attempt.

    Establishes WebSocket, authenticates, then calls on_connect_success.
    on_disconnect is called when session ends.

    Native WS-protocol ping/pong detects zombie TCP connections within
    ws_ping_interval + ws_ping_timeout instead of waiting for OS keepalive
    timeout. See remote/connection.py connect_once() for full rationale.
    """
    if ws_ping_timeout is None:
        ws_ping_timeout = min(ws_ping_interval / 2, 10.0)
    ws_url = _build_edge_ws_url(remote_config.url)
    remote_id = remote_config.stargate_id

    logger.info(f"🔌 Connecting to Edge {remote_id} at {ws_url}")

    async with websockets.connect(
        ws_url,
        max_size=None,
        ping_interval=ws_ping_interval,
        ping_timeout=ws_ping_timeout,
        close_timeout=5.0,
    ) as ws:
        try:
            if not await auth_client.authenticate(ws):
                logger.warning(f"Auth failed with Edge {remote_id}")
                return

            logger.info(f"✅ Connected to Edge {remote_id}")
            await on_connect_success(ws)
        finally:
            await on_disconnect()
