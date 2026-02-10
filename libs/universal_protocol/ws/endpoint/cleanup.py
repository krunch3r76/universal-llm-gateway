"""WebSocket stream cleanup orchestration.

Single responsibility: Orchestrate all cleanup steps for a WebSocket stream.
Split into focused helpers for each cleanup concern.
"""

from universal_logging import get_logger
from starlette.websockets import WebSocket

from universal_protocol.observability import set_streams_active
from universal_protocol.sse.core import format_sse
from universal_protocol.ws.lifecycle import StreamContext
from universal_protocol.ws.registry import stream_registry

logger = get_logger(__name__)


# =============================================================================
# Cleanup Helpers
# =============================================================================


async def try_send_error_frame(
    websocket: WebSocket,
    error_frame: dict | None,
    stream_id: str,
) -> None:
    """Best-effort send of error frame (async, non-blocking).

    Inputs:
        websocket: WebSocket connection
        error_frame: Error frame dict or None
        stream_id: For logging

    Side-effects:
        Sends SSE-formatted error frame if websocket still connected.
    """
    if error_frame is None:
        return
    if websocket.client_state.name == "DISCONNECTED":
        return

    try:
        error_sse = format_sse(error_frame)
        await websocket.send_text(error_sse)
        logger.info(f"Sent error frame: {error_frame.get('code')}")
    except Exception as e:
        logger.warning(f"Failed to send error frame for {stream_id}: {e}")


async def try_close_websocket(
    websocket: WebSocket,
    close_code: int,
    stream_id: str,
) -> None:
    """Best-effort close of WebSocket (async, non-blocking).

    Inputs:
        websocket: WebSocket connection
        close_code: WebSocket close code (1000=done, 1001=error)
        stream_id: For logging

    Precondition:
        close_code != -1 (already closed indicator)
    """
    if close_code == -1:
        return  # Already closed by fail_stream
    if websocket.client_state.name == "DISCONNECTED":
        return

    try:
        await websocket.close(code=close_code)
        logger.info(f"✅ Closed WebSocket {stream_id} with code {close_code}")
    except Exception as e:
        logger.warning(f"Failed to close WebSocket for {stream_id}: {e}")


def schedule_context_cleanup(
    context: StreamContext | None,
    stream_id: str,
) -> None:
    """Schedule context cleanup (sync, non-blocking).

    Inputs:
        context: StreamContext or None
        stream_id: For logging

    Side-effects:
        Calls context.cleanup_nowait() which cancels tasks and schedules
        background awaiter. Does NOT block.
    """
    if context is None:
        return

    try:
        context.cleanup_nowait()
        logger.info(f"✅ Context cleanup scheduled for {stream_id}")
    except Exception as e:
        logger.error(f"Error scheduling context cleanup for {stream_id}: {e}")


async def cleanup_registry_entry(stream_id: str) -> bool:
    """Cleanup registry entry (queue close + unregister).

    Inputs:
        stream_id: Stream identifier

    Outputs:
        True if entry was cleaned up, False if not found

    Side-effects:
        Calls stream_registry.cleanup_entry() (closes queue, unregisters)
    """
    if stream_id not in stream_registry:
        logger.debug(f"ℹ️ {stream_id} already removed from registry")
        return False

    try:
        cleaned = await stream_registry.cleanup_entry(stream_id)
        logger.info(
            f"✅ Registry cleanup for {stream_id}. Remaining: {len(stream_registry)}"
        )
        return cleaned
    except Exception as e:
        logger.error(f"❌ Failed to cleanup {stream_id} from registry: {e}")
        return False


def update_stream_metrics() -> None:
    """Update active streams metric.

    Reads current registry size and updates gauge.
    """
    set_streams_active(len(stream_registry))


# =============================================================================
# Main Orchestrator
# =============================================================================


async def cleanup_websocket_stream(
    stream_id: str,
    context: StreamContext | None,
    close_code: int,
    error_frame: dict | None,
    websocket: WebSocket,
    already_closed: bool = False,
) -> None:
    """Orchestrate complete cleanup for a WebSocket stream.

    Inputs:
        stream_id: Stream identifier
        context: StreamContext (may be None)
        close_code: WebSocket close code (1000=done, 1001=error, -1=already closed)
        error_frame: Error frame to send (None if clean exit)
        websocket: WebSocket connection
        already_closed: If True, skip send/close (already handled)

    Cleanup steps (order matters):
        1. Cancel via registry (signal + push frame, single API)
        2. Send error frame if present (skip if already_closed)
        3. Close WebSocket if not already closed
        4. Schedule context cleanup (non-blocking)
        5. Cleanup registry entry + update metrics

    Invariant: Non-blocking in finally block (uses cleanup_nowait).
    Idempotent: Safe to call multiple times.
    """
    logger.info(f"🎯 CLEANUP STARTED for {stream_id} (close_code={close_code})")

    # 1. Cancel via registry (unified API - replaces direct cancellation_event.set())
    stream_registry.cancel_entry(stream_id, reason="ws_cleanup")

    # 2 & 3: Skip send/close if already handled
    if not already_closed:
        await try_send_error_frame(websocket, error_frame, stream_id)
        await try_close_websocket(websocket, close_code, stream_id)

    # 4. Schedule context cleanup (sync, non-blocking)
    schedule_context_cleanup(context, stream_id)

    # 5. Registry cleanup + metrics
    await cleanup_registry_entry(stream_id)
    update_stream_metrics()

    logger.info(f"✅ CLEANUP COMPLETED for {stream_id}")
