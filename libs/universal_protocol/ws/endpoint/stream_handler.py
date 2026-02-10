"""WebSocket stream handler - orchestration only.

Single responsibility: Accept connection, orchestrate validation/loop/cleanup.
"""

from universal_logging import get_logger
from starlette.websockets import WebSocket

from .cleanup import cleanup_websocket_stream
from .stream_loop import read_and_send_frames
from .validation import validate_stream_entry

logger = get_logger(__name__)


async def stream_handler(websocket: WebSocket) -> None:
    """Handle WebSocket streaming connections (orchestration).

    Endpoint: WS /stream/{stream_id}
    Protocol: SSE format ("data: {json}\\n\\n")

    Flow:
        1. Accept connection
        2. Validate stream exists → extract (context, queue, cancellation_event)
        3. Transition to STREAMING state
        4. Read and send frames until terminal frame
        5. Cleanup (signal, close WS, context, registry, metrics)

    Cleanup ownership:
        - WebSocket handler: StreamContext.cleanup_nowait() (non-blocking)
        - Registry: queue.close(), unregister (via cleanup_entry)
        - Both paths idempotent

    Inputs:
        websocket: Starlette WebSocket connection
    """
    stream_id = websocket.path_params.get("stream_id", "unknown")
    context = None
    close_code = 1000
    error_frame = None
    already_closed = False

    try:
        # 1. Accept connection
        await websocket.accept()
        logger.info(f"Accepted WebSocket stream: {stream_id}")

        # 2. Validate and extract state
        state = await validate_stream_entry(stream_id, websocket)
        if state is None:
            close_code = -1
            already_closed = True
            return

        context = state.context
        queue = state.queue

        # 3. Transition to STREAMING
        context.transition_to_streaming()

        # 4. Read and send frames loop
        close_code, error_frame = await read_and_send_frames(
            stream_id, queue, websocket
        )

        if close_code == -1:
            already_closed = True

    except Exception as e:
        logger.exception(f"Unexpected error in stream handler {stream_id}: {e}")
        error_frame = {
            "t": "err",
            "code": "STREAM_HANDLER_ERROR",
            "message": f"Unexpected error: {e!s}",
            "source": "stream",
        }
        close_code = 1011
        logger.info(f"🚨 EXCEPTION for {stream_id}: {type(e).__name__}")

    finally:
        # 5. Cleanup (always happens, non-blocking)
        await cleanup_websocket_stream(
            stream_id,
            context,
            close_code,
            error_frame,
            websocket,
            already_closed=already_closed,
        )
