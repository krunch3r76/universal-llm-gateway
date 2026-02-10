"""Crash error logging with error isolation."""

from universal_logging import get_logger

logger = get_logger(__name__)


def log_crash_error(process_id: str, exit_code: int, error_message: str) -> None:
    """
    Log crash error with full error isolation.
    
    Never raises exceptions to prevent breaking process_ipc monitoring.
    
    Args:
        process_id: Process/model ID that crashed
        exit_code: Exit code of crashed process
        error_message: Error message from process_ipc
    """
    try:
        logger.error(
            f"🚨 [crash_callback] Process {process_id} crashed with exit code {exit_code}: {error_message}"
        )
    except Exception:
        # Even logging can fail - don't let it break the callback
        pass
