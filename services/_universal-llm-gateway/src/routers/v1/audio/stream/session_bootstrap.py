"""Session bootstrap for live audio WebSocket transcription connections.

Loads the Whisper model, creates a worker streaming session, and sends the
ready message including effective timeout limits to the connected client.
"""

from typing import Any

from fastapi import WebSocket

from .deps import logger
from .websocket_errors import send_websocket_error


async def bootstrap_stream_session(
    *,
    websocket: WebSocket,
    worker_controller,
    request_id: str,
    model: str,
    session_config: dict[str, Any],
    ready_message: dict[str, Any],
) -> str | None:
    """Ensure model is loaded, create stream session, and send ready payload."""
    logger.info(f"[{request_id}] Loading model: {model}")
    if not await worker_controller.ensure_model_loaded(model):
        await send_websocket_error(
            websocket,
            "model_load_failed",
            f"Failed to load model: {model}",
            close=True,
        )
        return None

    logger.info(f"[{request_id}] Session config: {session_config}")

    try:
        session_id = await worker_controller.call_rpc(
            model_id=model,
            method="create_stream_session",
            params={"config": session_config},
        )
    except Exception as exc:
        await send_websocket_error(
            websocket,
            "session_create_failed",
            f"Failed to create session: {exc}",
            close=True,
        )
        return None

    logger.info(f"[{request_id}] Created session: {session_id}")
    await websocket.send_json({**ready_message, "session_id": session_id})
    return session_id
