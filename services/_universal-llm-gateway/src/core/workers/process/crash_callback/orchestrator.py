"""
Crash callback orchestration - delegates to responsibility-focused modules.

Coordinates crash response with full error isolation.
"""

from ..state import ProcessState
from .events import publish_socket_cleanup_event
from .health import stop_health_monitoring
from .logging import log_crash_error
from .state import cleanup_supervisor_references, mark_worker_failed


async def handle_process_crash_callback(
    process_id: str,
    exit_code: int,
    error_message: str,
    state: ProcessState,
) -> None:
    """
    Orchestrate crash cleanup response (event-driven, fully isolated).

    Delegates to specialized modules for each cleanup responsibility.
    All operations are error-isolated to prevent breaking process_ipc.

    Invariant: ∀ cleanup_step: error_isolated ∧ logged

    Args:
        process_id: Process/model ID that crashed
        exit_code: Exit code of crashed process
        error_message: Error message from process_ipc
        state: Process state container

    Side Effects:
        - Logs crash error
        - Marks worker as failed in state
        - Stops health monitoring
        - Removes supervisor references
        - Publishes SocketCleanupRequested event (non-blocking)

    Note:
        Resource tracker updates happen via WORKER_CRASH_DETECTED event
        (emitted by process_ipc, bridged by ProcessCrashBridge).
    """
    # Step 1: Log crash (never raises)
    log_crash_error(process_id, exit_code, error_message)

    # Step 2: Mark worker as failed (isolated)
    mark_worker_failed(process_id, state)

    # Step 3: Stop health monitoring (isolated, async)
    await stop_health_monitoring(process_id, state)

    # Step 4: Clean up supervisor references (isolated)
    cleanup_supervisor_references(process_id, state)

    # Step 5: Publish socket cleanup event (isolated, non-blocking)
    publish_socket_cleanup_event(process_id)
