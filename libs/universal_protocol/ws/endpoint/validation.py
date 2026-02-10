"""Stream entry validation helpers.

Single responsibility: Validate stream existence and extract state.
"""

from universal_logging import get_logger
from starlette.websockets import WebSocket

from universal_protocol.sse.core import format_sse
from universal_protocol.ws.registry import stream_registry

from .state import StreamStateErr, StreamStateOk

logger = get_logger(__name__)


async def fail_stream(
    code: str,
    message: str,
    source: str,
    websocket: WebSocket,
) -> None:
    """Send error frame and close WebSocket.

    Helper for consistent error handling across all failure paths.

    Inputs:
        code: Error code (e.g., "STREAM_CLOSED", "QUEUE_TIMEOUT")
        message: Human-readable error message
        source: Error origin ("stream", "engine", "rpc")
        websocket: WebSocket connection to close

    Protocol:
        1. Send error frame: data: {"t":"err",...}\\n\\n
        2. Close WebSocket with code 1001
        3. No further sends after close

    One-shot semantics: Best-effort send, proceed to close regardless.
    """
    try:
        error_frame = format_sse(
            {
                "t": "err",
                "code": code,
                "message": message,
                "source": source,
                "data": {},
            }
        )
        await websocket.send_text(error_frame)
        logger.info(f"Sent error frame: code={code}, message={message}")
    except Exception as e:
        logger.warning(f"Failed to send error frame: {e}")
    finally:
        try:
            await websocket.close(code=1001)
            logger.info("Closed WebSocket with code 1001")
        except Exception as e:
            logger.warning(f"Failed to close WebSocket: {e}")


def get_stream_state(stream_id: str) -> StreamStateOk | StreamStateErr:
    """Pure lookup: get stream state from registry.

    Inputs:
        stream_id: Stream identifier

    Outputs:
        StreamStateOk: Valid state with context, queue, cancellation_event
        StreamStateErr: Error with code and message

    Invariant: No I/O, no side-effects, pure function.
    """
    entry = stream_registry.get(stream_id)
    if entry is None:
        return StreamStateErr(
            code="INVALID_STREAM_ID", message=f"Unknown stream_id {stream_id}"
        )

    context = entry.context
    queue = entry.queue
    cancellation_event = entry.cancellation_event

    if context is None or queue is None:
        return StreamStateErr(
            code="INVALID_STREAM_STATE",
            message=f"Stream {stream_id} missing context or queue",
        )

    return StreamStateOk(
        context=context, queue=queue, cancellation_event=cancellation_event
    )


async def validate_stream_entry(
    stream_id: str,
    websocket: WebSocket,
) -> StreamStateOk | None:
    """Validate stream exists and extract state.

    Inputs:
        stream_id: Stream identifier from path params
        websocket: WebSocket connection

    Outputs:
        StreamStateOk: Valid state with context, queue, cancellation_event
        None: Validation failed, websocket already closed via fail_stream

    Design:
        Returns None instead of raising to avoid double-send/close bugs.
        Caller checks for None to skip cleanup send/close.
    """
    result = get_stream_state(stream_id)

    match result:
        case StreamStateErr(code=code, message=message):
            await fail_stream(
                code=code,
                message=message,
                source="stream",
                websocket=websocket,
            )
            return None
        case StreamStateOk():
            return result
