"""
Message I/O loops for Local Edge WebSocket client.

Handles sending, receiving, and ping/pong.
Reuses patterns from remote/message_io.py.

INVARIANT: ∀ outbound via bounded_queue.try_put()
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


async def local_sender_loop(
    *,
    websocket: WebSocketClientProtocol,
    send_queue: BoundedQueue,
    is_running: Callable[[], bool],
    peer_id: str,
) -> None:
    """
    Dedicated sender task that dequeues and sends messages.

    Same pattern as remote/message_io.py sender_loop.
    """
    logger.debug(f"🚀 Started sender loop for Edge {peer_id}")

    while is_running() and websocket:
        try:
            message = await asyncio.wait_for(
                send_queue.get(),
                timeout=1.0,
            )

            if websocket:
                logger.debug(f"📤 Sending to Edge: type={message.get('type')}")
                await websocket.send(json.dumps(message))

        except TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Send to Edge failed: {e}")


async def local_receive_loop(
    *,
    websocket: WebSocketClientProtocol,
    peer_id: str,
    on_telemetry: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None,
    on_measurement_request: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    | None = None,
) -> None:
    """
    Receive and dispatch messages from Edge.

    Handles ping/pong, measurement requests, and forwards telemetry.
    """
    if not websocket:
        logger.warning("🔌 Local receive loop: no websocket, exiting")
        return

    logger.debug("🔌 Local receive loop: starting")

    try:
        async for raw in websocket:
            try:
                msg_dict = json.loads(raw)
                msg = parse_federation_message(msg_dict)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Invalid message from Edge: {e}")
                continue

            msg_type = msg.type
            data = msg.data

            if msg_type == FederationMessageType.FEDERATION_PONG.value:
                continue

            if msg_type == FederationMessageType.FEDERATION_PING.value:
                pong = create_federation_pong()
                await websocket.send(json.dumps(pong.to_dict()))
                continue

            if msg_type == FederationMessageType.MEASUREMENT_VRAM_REQUEST.value:
                await _handle_measurement_request(
                    websocket, data, on_measurement_request
                )
                continue

            if on_telemetry and msg_type:
                logger.debug(
                    f"📨 Received from Edge: type={msg_type}, forwarding to callback"
                )
                await on_telemetry(peer_id, msg_type, data)
            else:
                if not on_telemetry:
                    logger.warning(f"❌ No callback registered for type={msg_type}")
                if not msg_type:
                    logger.warning("❌ Empty type received from Edge")

    except asyncio.CancelledError:
        logger.info("🔌 Local receive loop cancelled")
        raise
    except Exception as e:
        logger.error(f"🔌 Local receive loop error: {e}")
        raise


async def _handle_measurement_request(
    websocket: WebSocketClientProtocol,
    data: dict[str, Any],
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
    """Dispatch measurement request to handler, send response back via WS."""
    from ....common.protocol import create_measurement_vram_response

    request_id = data.get("request_id", "")
    if not handler:
        logger.warning("No measurement handler registered, sending error")
        resp = create_measurement_vram_response(
            request_id, None, None, error="No measurement handler"
        )
        await websocket.send(json.dumps(resp.to_dict()))
        return

    try:
        result = await handler(data)
        resp = create_measurement_vram_response(
            request_id=request_id,
            total_mb=result.get("total_mb"),
            process_count=result.get("process_count"),
        )
    except Exception as exc:
        logger.error(f"Measurement handler error: {exc}")
        resp = create_measurement_vram_response(request_id, None, None, error=str(exc))

    await websocket.send(json.dumps(resp.to_dict()))


async def local_ping_loop(
    *,
    send_queue: BoundedQueue,
    ping_interval: float,
    is_running: Callable[[], bool],
    has_websocket: Callable[[], bool],
) -> None:
    """
    Periodic ping to keep connection alive.

    Same pattern as remote/message_io.py ping_loop.
    """
    while is_running() and has_websocket():
        await asyncio.sleep(ping_interval)

        try:
            message = create_federation_ping().to_dict()
            if not send_queue.try_put(message):
                logger.warning("Ping queue full")
        except Exception as e:
            logger.warning(f"Ping failed: {e}")
