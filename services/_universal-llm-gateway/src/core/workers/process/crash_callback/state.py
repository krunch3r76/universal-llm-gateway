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
    try:
        state.failed_workers.add(process_id)
    except Exception as e:
        logger.error(f"Failed to mark worker {process_id} as failed: {e}")


def cleanup_supervisor_references(process_id: str, state: ProcessState) -> None:
    """
    Remove supervisor and socket path from state tracking.
    
    Error isolated - failures logged but don't propagate.
    
    Args:
        process_id: Process/model ID to clean up
        state: Process state container
    """
    try:
        _ = state.remove_supervisor(process_id)
        _ = state.remove_socket_path(process_id)
        logger.info(f"🧹 Cleaned up supervisor references for {process_id}")
    except Exception as e:
        logger.error(f"Failed to cleanup supervisor references for {process_id}: {e}")
