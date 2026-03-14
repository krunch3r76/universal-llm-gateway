"""
WebSocket connection lifecycle for Local Edge client.

CRITICAL: Bounded backoff (30s max) per FED-11 invariant.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import websockets
from universal_logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from websockets.client import WebSocketClientProtocol

    from ....common.config import LocalEdgeConfig
    from .auth import LocalEdgeAuthClient

logger = get_logger(__name__)


async def local_connection_loop(
    *,
    config: LocalEdgeConfig,
    auth_client: LocalEdgeAuthClient,
    initial_delay: float,
    max_delay: float,
    jitter_factor: float,
    is_running: Callable[[], bool],
    on_connect_success: Callable[[WebSocketClientProtocol], Awaitable[None]],
    on_disconnect: Callable[[], Awaitable[None]],
) -> None:
    """
    Main connection loop with bounded backoff for Unix socket.

    Args:
        config: Local Edge configuration
        auth_client: Authentication client
        initial_delay: Initial reconnect delay (seconds)
        max_delay: Maximum reconnect delay (seconds, capped at 30s)
        jitter_factor: Delay jitter factor (e.g., 0.1 = ±10%)
        is_running: Callback to check if client should continue
        on_connect_success: Callback when authenticated (receives websocket)
        on_disconnect: Callback when disconnected
    """
    current_delay = initial_delay

    logger.info(f"🔄 Local Edge connection loop started (socket={config.socket_path})")

    while is_running():
        try:
            await _connect_once(
                config=config,
                auth_client=auth_client,
                on_connect_success=on_connect_success,
                on_disconnect=on_disconnect,
            )
            # Reset delay on successful session
            current_delay = initial_delay
        except asyncio.CancelledError:
            logger.info("Local Edge connection loop cancelled")
            break
        except Exception as e:
            logger.warning(f"Connection to Edge failed: {e}")

        if is_running():
            # Apply jitter to delay (±jitter_factor)
            jitter = current_delay * jitter_factor
            delay = current_delay + random.uniform(-jitter, jitter)

            logger.info(f"Reconnecting to Edge in {delay:.1f}s (max {max_delay}s)")
            await asyncio.sleep(delay)

            # Exponential backoff (CAPPED at max - 30s per FED-11)
            current_delay = min(current_delay * 2, max_delay)


async def _connect_once(
    *,
    config: LocalEdgeConfig,
    auth_client: LocalEdgeAuthClient,
    on_connect_success: Callable[[WebSocketClientProtocol], Awaitable[None]],
    on_disconnect: Callable[[], Awaitable[None]],
) -> None:
    """
    Single connection attempt over Unix socket.

    Establishes WebSocket, authenticates, then calls on_connect_success.
    on_disconnect is called when session ends.
    """

    socket_path = config.socket_path
    # URI is required by websockets library but not used for routing over Unix socket
    uri = "ws://localhost/ws/federation/edge"

    logger.info(f"🔌 Connecting to Edge via Unix socket: {socket_path}")

    try:
        async with websockets.unix_connect(
            path=socket_path,
            uri=uri,
            max_size=None,
            ping_interval=None,  # We handle our own ping/pong
            ping_timeout=None,
            close_timeout=5.0,
        ) as ws:
            logger.debug("🔌 Unix socket connection established")

            try:
                # Authenticate (same protocol as Remote→Master)
                if not await auth_client.authenticate(ws):
                    logger.warning("Auth failed with Edge")
                    return

                logger.info(f"✅ Connected to Edge {config.stargate_id}")

                # Hand off to caller for message loops
                await on_connect_success(ws)

            finally:
                await on_disconnect()

    except FileNotFoundError:
        logger.warning(f"Edge socket not found: {socket_path}")
        raise
    except ConnectionRefusedError:
        logger.warning(f"Edge refused connection: {socket_path}")
        raise
