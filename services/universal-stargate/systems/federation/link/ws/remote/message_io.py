"""
Message I/O loops for Remote WebSocket client.

Handles sending, receiving, and ping/pong.

INVARIANT: ∀ outbound via bounded_queue.try_put()
INVARIANT: sustained_overflow ⟹ disconnect ∧ schedule_reconnect
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ....common.protocol import (
    FederationMessageType,
    create_federation_ping,
    create_federation_pong,
    parse_federation_message,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from universal_protocol.ws.bounded_queue import BoundedQueue
    from websockets.client import WebSocketClientProtocol

logger = get_logger(__name__)


async def sender_loop(
    *,
    websocket: WebSocketClientProtocol,
    send_queue: BoundedQueue,
    is_running: Callable[[], bool],
    stargate_id: str,
) -> None:
    """
    Dedicated sender task that dequeues and sends messages.

    Runs until websocket closes or client stops.

    Args:
        websocket: Active WebSocket connection
        send_queue: Bounded queue for outgoing messages
        is_running: Callback to check if client is running
        stargate_id: Local stargate ID (for logging)
    """
    logger.debug(f"🚀 Started sender loop for Remote {stargate_id}")
    while is_running() and websocket:
        try:
            message = await asyncio.wait_for(
                send_queue.get(),
                timeout=1.0,
            )

            if websocket:
                logger.debug(f"📤 Sending message: type={message.get('type')}")
                await websocket.send(json.dumps(message))
                logger.debug("✅ Message sent successfully")

        except TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Send failed: {e}")


async def receive_loop(
    *,
    websocket: WebSocketClientProtocol,
    peer_id: str,
    on_telemetry: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None,
    on_cancel: Callable[[str, str | None], Awaitable[bool]] | None,
    send_pong: Callable[[WebSocketClientProtocol], Awaitable[None]],
) -> None:
    """
    Receive and dispatch messages from Master.

    Handles ping/pong, cancel messages, and forwards telemetry to callback.

    Args:
        websocket: Active WebSocket connection
        peer_id: Master's stargate ID
        on_telemetry: Callback for incoming telemetry (remote_id, msg_type, data)
        on_cancel: Callback for cancel messages (request_id, model_id) -> success
        send_pong: Callback to send pong response
    """
    if not websocket:
        logger.warning("🔌 Receive loop: no websocket, exiting")
        return

    logger.debug("🔌 Receive loop: starting async for loop over websocket")
    try:
        async for raw in websocket:
            try:
                msg_dict = json.loads(raw)
                msg = parse_federation_message(msg_dict)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Invalid message from Master: {e}")
                continue

            msg_type = msg.type
            data = msg.data

            if msg_type == FederationMessageType.FEDERATION_PONG.value:
                continue  # Pong received - connection alive

            if msg_type == FederationMessageType.FEDERATION_PING.value:
                # Respond with pong
                await send_pong(websocket)
                continue

            # Handle cancel messages
            if msg_type == FederationMessageType.REQUEST_CANCEL.value:
                request_id = data.get("request_id")
                model_id = data.get("model_id")
                if request_id and on_cancel:
                    success = await on_cancel(request_id, model_id)
                    logger.info(
                        f"🛑 Cancel request {request_id[:8]}...: "
                        f"{'success' if success else 'not found'}"
                    )
                elif not request_id:
                    logger.warning("Cancel message missing request_id")
                continue

            # Forward to callback if present
            if on_telemetry:
                await on_telemetry(peer_id, msg_type, data)
    except asyncio.CancelledError:
        logger.warning("🔌 Receive loop: CANCELLED (likely during shutdown)")
        raise
    except Exception as e:
        logger.error(f"🔌 Receive loop: exception {type(e).__name__}: {e}")
        raise
    finally:
        logger.debug("🔌 Receive loop: exited async for loop")


async def ping_loop(
    *,
    send_queue: BoundedQueue,
    ping_interval: float,
    is_running: Callable[[], bool],
    has_websocket: Callable[[], bool],
) -> None:
    """
    Periodic ping to keep connection alive.

    Args:
        send_queue: Bounded queue for outgoing messages
        ping_interval: Seconds between pings
        is_running: Callback to check if client is running
        has_websocket: Callback to check if websocket is connected
    """
    while is_running() and has_websocket():
        await asyncio.sleep(ping_interval)

        try:
            message = create_federation_ping().to_dict()
            if not send_queue.try_put(message):
                logger.warning("Ping queue full")
        except Exception as e:
            logger.warning(f"Ping failed: {e}")


async def send_pong(websocket: WebSocketClientProtocol) -> None:
    """Send pong response to ping."""
    pong = create_federation_pong()
    await websocket.send(json.dumps(pong.to_dict()))
