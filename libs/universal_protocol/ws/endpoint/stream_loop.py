"""Stream frame read-and-send loop (pure event processor).

Single responsibility: Read events from queue, dispatch by type, send to WebSocket.
No timeout logic - idle monitor handles expiration via queue events.
"""

from universal_logging import get_logger
from starlette.websockets import WebSocket

from universal_protocol.sse.core import format_sse
from universal_protocol.ws.frame_types import (
    FRAME_DONE,
    FRAME_ERR,
    CODE_QUEUE_CLOSED,
    CODE_STREAM_ERROR,
    get_close_code,
    is_terminal_frame,
)
from universal_protocol.ws.queue_protocol import StreamQueueProtocol
from universal_protocol.ws.registry import stream_registry

logger = get_logger(__name__)


def update_consumer_activity(stream_id: str) -> None:
    """Update last_activity timestamp after successful frame delivery."""
    entry = stream_registry.get(stream_id)
    if entry:
        entry.update_activity()


async def send_frame(websocket: WebSocket, frame: dict) -> None:
    """Send SSE-formatted frame via WebSocket."""
    frame_sse = format_sse(frame)
    await websocket.send_text(frame_sse)
    logger.debug(f"Sent frame type: {frame.get('t')}")


def log_terminal_frame(stream_id: str, frame: dict) -> None:
    """Log terminal frame details."""
    frame_type = frame.get("t")
    
    if frame_type == FRAME_DONE:
        logger.info(f"Stream {stream_id} completed: {frame.get('usage', {})}")
    else:
        logger.warning(
            f"Stream {stream_id} terminal: type={frame_type}, "
            f"code={frame.get('code')}, message={frame.get('message')}"
        )


async def read_and_send_frames(
    stream_id: str,
    queue: StreamQueueProtocol,
    websocket: WebSocket,
) -> tuple[int, dict | None]:
    """Read frames from queue and send over WebSocket until terminal frame.
    
    Pure event processor - no timeout logic. Waits indefinitely on queue.get().
    Idle monitor provides safety net by pushing idle_timeout events.
    
    Inputs:
        stream_id: Stream identifier (for logging + registry lookup)
        queue: Queue to read frames from
        websocket: WebSocket connection to send to
    
    Outputs:
        (close_code, error_frame_or_none)
            close_code: 1000 (done), 1001 (error/control), -1 (already handled)
            error_frame: dict if error to send, None if clean exit
    
    Invariant:
        ∀ frame ∈ queue: frame sent via websocket ⟹ last_activity updated
        Handler exits ⟺ terminal frame received (t="done" or t="err")
    """
    close_code = 1000
    error_frame = None
    
    while True:
        try:
            # Pure await - no timeout (idle monitor handles expiration)
            frame = await queue.get()
            
            # Send frame to client
            await send_frame(websocket, frame)
            
            # Update activity after successful send
            update_consumer_activity(stream_id)
            
            # Check for terminal frame
            if is_terminal_frame(frame):
                close_code = get_close_code(frame)
                log_terminal_frame(stream_id, frame)
                
                # Return error frame if it's an error type (for cleanup to send)
                if frame.get("t") != FRAME_DONE:
                    error_frame = frame
                break
        
        except RuntimeError as e:
            if "Queue is closed" in str(e):
                logger.warning(f"Queue closed for stream {stream_id}")
                error_frame = {
                    "t": FRAME_ERR,
                    "code": CODE_QUEUE_CLOSED,
                    "message": "Stream terminated: queue closed",
                    "source": "stream",
                }
                close_code = 1001
                break
            raise
        
        except Exception as e:
            logger.exception(f"Error in stream loop: {e}")
            error_frame = {
                "t": FRAME_ERR,
                "code": CODE_STREAM_ERROR,
                "message": f"Error processing frame: {e!s}",
                "source": "stream",
            }
            close_code = 1001
            break
    
    logger.info(f"📍 EXITED MAIN LOOP for {stream_id}")
    return close_code, error_frame
