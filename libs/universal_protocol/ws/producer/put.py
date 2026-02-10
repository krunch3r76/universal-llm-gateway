"""Producer put operation - enqueue frame to stream queue.

Single responsibility: Lookup stream, update activity, enqueue frame.
"""

from universal_logging import get_logger
from universal_protocol.ws.registry import stream_registry

from .error_frame import make_producer_error_frame

logger = get_logger(__name__)


async def producer_put(stream_id: str, frame: dict) -> bool:
    """Enqueue frame to stream queue (producer side).

    Inputs:
        stream_id: Stream identifier
        frame: Frame dict to enqueue

    Outputs:
        True if successfully queued, False if error occurred

    Side-effects:
        - Updates entry.last_activity timestamp
        - Enqueues frame to entry.queue
        - Best-effort enqueue of error frame on failure
    """
    entry = stream_registry.get(stream_id)
    if not entry:
        logger.error(f"Producer: stream {stream_id} not found")
        return False

    # Update activity timestamp
    entry.update_activity()

    if not entry.queue:
        logger.error(f"Producer: stream {stream_id} has no queue")
        return False

    try:
        await entry.queue.put(frame)
        return True

    except RuntimeError as e:
        # Queue closed
        logger.warning(f"Queue closed for stream {stream_id}: {e}")
        return False

    except Exception as e:
        # Other producer errors
        logger.exception(f"Producer error for stream {stream_id}: {e}")

        # Best-effort enqueue error frame
        error_frame = make_producer_error_frame(stream_id, e)
        try:
            await entry.queue.put(error_frame)
        except Exception:
            logger.debug(f"Failed to enqueue error frame for {stream_id}")

        return False
