"""WebSocket error helpers for live audio transcription sessions.

Sends structured JSON error payloads to connected clients and optionally closes
the socket when policy violations or unrecoverable streaming failures occur.
"""

from fastapi import WebSocket
from starlette.websockets import WebSocketState


async def send_websocket_error(
    websocket: WebSocket, code: str, message: str, *, close: bool = False
) -> None:
    """Send an error JSON message and optionally close the WebSocket."""
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(
                {"type": "error", "code": code, "message": message}
            )
            if close:
                await websocket.close(code=1008)
    except Exception:
        pass
