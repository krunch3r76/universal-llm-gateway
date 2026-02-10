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
    """
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
) -> None:
    """
    Single connection attempt.

    Establishes WebSocket, authenticates, then calls on_connect_success.
    on_disconnect is called when session ends.
    """
    ws_url = _build_edge_ws_url(remote_config.url)
    remote_id = remote_config.stargate_id

    logger.info(f"🔌 Connecting to Edge {remote_id} at {ws_url}")

    async with websockets.connect(
        ws_url,
        ping_interval=None,  # We handle our own ping/pong
        ping_timeout=None,
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
