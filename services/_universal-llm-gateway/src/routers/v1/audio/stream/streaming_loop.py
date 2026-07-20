"""Main receive/process loop for live audio WebSocket transcription sessions.

Receives PCM chunks from the client, validates them, forwards RPC calls to the
Whisper worker, and streams transcription JSON results back over the socket.
"""

import asyncio
import base64

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from ..error_mapper import map_worker_error
from ..session_utils import MONITOR_MODE_RECEIVE_TIMEOUT_S
from .chunk_validation import validate_audio_chunk
from .deps import logger
from .websocket_errors import send_websocket_error


async def run_streaming_loop(
    *,
    websocket: WebSocket,
    worker_controller,
    request_id: str,
    model: str,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    session_start: float,
    effective_session_timeout: int,
    effective_inactivity_timeout: int,
) -> None:
    """Process audio chunks until disconnect, timeout, or unrecoverable error."""
    consecutive_errors = 0

    while True:
        if effective_session_timeout > 0:
            elapsed = loop.time() - session_start
            if elapsed > effective_session_timeout:
                await send_websocket_error(
                    websocket,
                    "session_timeout",
                    f"Max session duration ({effective_session_timeout}s) exceeded",
                    close=True,
                )
                break

        try:
            audio_bytes = await _receive_audio_bytes(
                websocket, effective_inactivity_timeout
            )
        except TimeoutError:
            logger.info(
                f"[{request_id}] Inactivity timeout "
                f"({effective_inactivity_timeout}s) - no audio"
            )
            await send_websocket_error(
                websocket,
                "inactivity_timeout",
                f"No audio for {effective_inactivity_timeout}s",
                close=True,
            )
            break
        except WebSocketDisconnect:
            logger.info(f"[{request_id}] Client disconnected")
            break

        if audio_bytes is None:
            continue

        validation = validate_audio_chunk(audio_bytes, consecutive_errors)
        if not validation.valid:
            consecutive_errors = validation.consecutive_errors
            await send_websocket_error(
                websocket,
                validation.error_code or "invalid_chunk",
                validation.error_message or "Invalid audio chunk",
                close=validation.should_close,
            )
            if validation.should_close:
                break
            continue

        consecutive_errors = 0

        try:
            results = await worker_controller.call_rpc(
                model_id=model,
                method="process_audio_chunk",
                params={
                    "session_id": session_id,
                    "audio_bytes": base64.b64encode(audio_bytes).decode(),
                },
            )
        except Exception as exc:
            logger.warning(f"[{request_id}] RPC error: {exc}")
            error_code, error_message, should_close = map_worker_error(exc)
            await send_websocket_error(
                websocket, error_code, error_message, close=should_close
            )
            if should_close:
                break
            continue

        for result in results:
            await websocket.send_json({"type": "transcription", **result})


async def _receive_audio_bytes(
    websocket: WebSocket, effective_inactivity_timeout: int
) -> bytes | None:
    """Receive the next audio chunk, honoring inactivity or monitor-mode timeouts."""
    if effective_inactivity_timeout > 0:
        return await asyncio.wait_for(
            websocket.receive_bytes(), timeout=effective_inactivity_timeout
        )

    try:
        return await asyncio.wait_for(
            websocket.receive_bytes(), timeout=MONITOR_MODE_RECEIVE_TIMEOUT_S
        )
    except TimeoutError:
        return None
