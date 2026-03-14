"""
WebSocket connection lifecycle for Remote client.

CRITICAL: Bounded backoff (30s max) per FED-11 invariant.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import websockets
from universal_logging import get_logger
from websockets.client import WebSocketClientProtocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ....common.config.schema import MasterStargateConfig
    from .auth import RemoteAuthClient

logger = get_logger(__name__)


async def connection_loop(
    *,
    master_config: MasterStargateConfig,
    auth_client: RemoteAuthClient,
    stargate_id: str,
    initial_delay: float,
    max_delay: float,
    jitter_factor: float,
    is_running: Callable[[], bool],
    set_running: Callable[[bool], None],
    on_connect_success: Callable[[WebSocketClientProtocol], Awaitable[None]],
    on_disconnect: Callable[[], Awaitable[None]],
) -> None:
    """
    Main connection loop with bounded backoff.

    Args:
        master_config: Master Stargate configuration
        auth_client: Authentication client
        stargate_id: Local stargate ID
        initial_delay: Initial reconnect delay (seconds)
        max_delay: Maximum reconnect delay (seconds, capped at 30s)
        jitter_factor: Delay jitter factor (e.g., 0.1 = ±10%)
        is_running: Callback to check if client should continue
        set_running: Callback to set running state
        on_connect_success: Callback when authenticated (receives websocket)
        on_disconnect: Callback when disconnected
    """
    # Ensure max_delay adheres to FED-11 invariant (30s max).
    max_delay = min(max_delay, 30.0)
    current_delay = initial_delay

    logger.info(f"🔄 Connection loop STARTED (running={is_running()})")
    while is_running():
        try:
            logger.debug(f"🔄 Calling connect_once() (running={is_running()})")
            await connect_once(
                master_config=master_config,
                auth_client=auth_client,
                stargate_id=stargate_id,
                on_connect_success=on_connect_success,
                on_disconnect=on_disconnect,
            )
            # Reset delay on successful session
            current_delay = initial_delay
            logger.debug(f"🔄 connect_once() returned (running={is_running()})")
        except asyncio.CancelledError:
            logger.error(
                "🚨 CONNECTION LOOP CANCELLED - "
                f"should only happen during shutdown! (running={is_running()})"
            )
            break
        except Exception as e:
            logger.warning(f"Connection to Master failed: {e}")

        if is_running():
            # Apply jitter to delay (±jitter_factor)
            jitter = current_delay * jitter_factor
            delay = current_delay + random.uniform(-jitter, jitter)

            logger.info(f"Reconnecting to Master in {delay:.1f}s (max {max_delay}s)")
            await asyncio.sleep(delay)

            # Exponential backoff (CAPPED at max - 30s per FED-11)
            current_delay = min(current_delay * 2, max_delay)
        else:
            logger.warning(
                "⚠️ Connection loop: _running=False, exiting reconnection logic"
            )

    logger.error(
        "🚨 CONNECTION LOOP EXITED - Remote is shutting down! "
        f"(final _running={is_running()})"
    )


async def connect_once(
    *,
    master_config: MasterStargateConfig,
    auth_client: RemoteAuthClient,
    stargate_id: str,
    on_connect_success: Callable[[WebSocketClientProtocol], Awaitable[None]],
    on_disconnect: Callable[[], Awaitable[None]],
) -> None:
    """
    Single connection attempt.

    Establishes WebSocket, authenticates, then calls on_connect_success.
    on_disconnect is called when session ends (before raising exceptions).
    """
    ws_url = master_config.url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}/ws/federation/master"

    logger.info(f"🔌 Connecting to Master at {ws_url}")

    try:
        async with websockets.connect(
            ws_url,
            max_size=None,
            ping_interval=None,  # We handle our own ping/pong
            ping_timeout=None,
            close_timeout=5.0,
        ) as ws:
            logger.debug("🔌 WebSocket connection established")

            try:
                # Authenticate (Remote sends first)
                logger.debug("🔐 Starting authentication...")
                if not await auth_client.authenticate(ws):
                    logger.warning("Auth failed with Master")
                    return

                logger.info(f"✅ Connected to Master {master_config.stargate_id}")

                # Hand off to caller for message loops
                await on_connect_success(ws)

            finally:
                logger.debug("🔌 Cleaning up connection resources...")
                await on_disconnect()

    except asyncio.CancelledError:
        logger.error("🚨 connect_once() CANCELLED - propagating to connection_loop()")
        raise
    except Exception as e:
        logger.error(f"🔌 Connection attempt failed: {type(e).__name__}: {e}")
        raise
