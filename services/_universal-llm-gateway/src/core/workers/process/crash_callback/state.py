"""Crash state management - worker failure marking and supervisor cleanup."""

from universal_logging import get_logger

from ..state import ProcessState

logger = get_logger(__name__)


def mark_worker_failed(process_id: str, state: ProcessState) -> None:
    """
    Mark worker as failed for immediate cleanup.

    Error isolated - failures logged but don't propagate.

    Args:
        process_id: Process/model ID to mark as failed
        state: Process state container
    """
    state.failed_workers.add(process_id)


def cleanup_supervisor_references(process_id: str, state: ProcessState) -> None:
    """
    Remove supervisor and socket path from state tracking.

    Error isolated - failures logged but don't propagate.

    Args:
        process_id: Process/model ID to clean up
        state: Process state container
    """
    state.remove_supervisor(process_id)
    state.remove_socket_path(process_id)
    state.remove_engine_pid(process_id)
    logger.info("Cleaned up supervisor references for %s", process_id)
