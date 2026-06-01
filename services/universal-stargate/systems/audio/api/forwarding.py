"""WebSocket message forwarding for audio streaming proxy."""

import asyncio
import json

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from universal_logging import get_logger
from websockets.legacy.client import WebSocketClientProtocol

logger = get_logger(__name__)


async def forward_client_to_gateway(
    client_ws: WebSocket,
    gateway_ws: WebSocketClientProtocol,
    session_id: str,
    gateway_ready: asyncio.Event,
) -> None:
    """
    Forward messages from client to Gateway.

    Drops all client traffic until the Gateway signals readiness to prevent
    buffering audio while the transcription model is still loading.
    """
    warned_not_ready = False
    dropped_frame_count = 0
    frames_since_last_log = 0
    log_every_n_drops = 10  # Log every 10 dropped frames to avoid spam

    try:
        while True:
            message = await client_ws.receive()
            message_type = message.get("type")

            if message_type == "websocket.receive":
                if not gateway_ready.is_set():
                    dropped_frame_count += 1
                    frames_since_last_log += 1

                    if not warned_not_ready:
                        logger.info(
                            f"[{session_id}] Dropping client frames until"
                            f"gateway ready f"
                            "(model loading)"
                        )
                        warned_not_ready = True
                    elif frames_since_last_log >= log_every_n_drops:
                        logger.debug(
                            f"[{session_id}] Dropped {dropped_frame_count} frames "
                            "(gateway not ready)"
                        )
                        frames_since_last_log = 0
                    continue

                if dropped_frame_count > 0:
                    logger.info(
                        f"[{session_id}] Gateway ready - dropped {dropped_frame_count} "
                        "pre-ready frames, now forwarding"
                    )
                    dropped_frame_count = 0

                if "bytes" in message:
                    await gateway_ws.send(message["bytes"])
                elif "text" in message:
                    await gateway_ws.send(message["text"])
            elif message_type == "websocket.disconnect":
                logger.debug(f"[{session_id}] Client disconnected")
                break
    except WebSocketDisconnect:
        logger.debug(f"[{session_id}] Client WebSocket disconnected")
    except Exception as e:
        logger.debug(f"[{session_id}] Client→Gateway forward ended: {e}")


async def forward_gateway_to_client(
    gateway_ws: WebSocketClientProtocol,
    client_ws: WebSocket,
    session_id: str,
    gateway_ready: asyncio.Event,
) -> None:
    """
    Forward messages from Gateway to client.

    Note: Gateway sends a 'ready' message with session config including
    resolved VAD parameters, which is transparently forwarded to client
    for configuration visibility.
    """
    try:
        async for message in gateway_ws:
            if client_ws.client_state != WebSocketState.CONNECTED:
                logger.debug(f"[{session_id}] Client not connected, stopping forward")
                break

            if not gateway_ready.is_set() and isinstance(message, str):
                payload: (
                    dict[str, object] | list[object] | str | int | float | bool | None
                ) = None
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    payload = None

                if isinstance(payload, dict) and payload.get("type") == "ready":
                    gateway_ready.set()

            if isinstance(message, bytes):
                await client_ws.send_bytes(message)
            else:
                await client_ws.send_text(message)
    except websockets.ConnectionClosed as e:
        logger.debug(f"[{session_id}] Gateway closed: code={e.code}, reason={e.reason}")
    except Exception as e:
        logger.debug(f"[{session_id}] Gateway→Client forward ended: {e}")


async def send_error(websocket: WebSocket, code: str, message: str) -> None:
    """
    Send error message to client if still connected.

    Swallows exceptions to prevent error-on-error cascades.
    """
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": code,
                    "message": message,
                }
            )
    except Exception as e:
        logger.debug(f"Failed to send error to client: {e}")
