"""Error handling for worker communication."""

import traceback

from process_ipc.core.exceptions import ProcessError
from universal_logging import get_logger

from ....errors import WorkerInitializationError

logger = get_logger(__name__)


def log_process_error(
    error: ProcessError,
    model_id: str,
    command_type: str,
) -> None:
    """
    Log ProcessError with appropriate detail level.

    Connection errors are logged without full traceback,
    other errors get context and conditional traceback.

    Args:
        error: ProcessError instance
        model_id: Model identifier
        command_type: Command type that failed
    """
    worker_id = getattr(error, "process_id", model_id)
    error_details = getattr(error, "details", {})
    error_str = str(error).lower()

    # Check if this is a connection error - handle gracefully
    is_connection_error = (
        "transport connection lost" in error_str
        or "transport not connected" in error_str
        or "connection closed" in error_str
        or "connection lost" in error_str
    )

    if is_connection_error:
        # Connection error - log clearly without full traceback
        logger.error(
            f"❌ Connection lost sending command '{command_type}' to worker {worker_id}: {error}"
        )
        logger.error(f"  Worker ID: {worker_id}")
        logger.error(f"  Command type: {command_type}")
    else:
        # Other errors - log with context
        logger.error(
            f"❌ Process error sending command '{command_type}' to worker {worker_id}: {error}"
        )
        logger.error(f"  Worker ID: {worker_id}")
        logger.error(f"  Error details: {error_details}")
        logger.error(f"  Command type: {command_type}")
        # Only show traceback for unexpected errors (not timeout)
        if not ("timeout" in error_str or "timed out" in error_str):
            logger.debug(f"Full traceback for '{command_type}' error:", exc_info=True)


def create_worker_error_from_process_error(
    error: ProcessError,
    model_id: str,
    command: dict,
    operation: str,
) -> WorkerInitializationError:
    """
    Create WorkerInitializationError from ProcessError.

    Args:
        error: ProcessError instance
        model_id: Model identifier
        command: Command that failed
        operation: Operation name

    Returns:
        WorkerInitializationError with full context
    """
    command_type = command.get("command_type", "unknown")
    worker_id = getattr(error, "process_id", model_id)
    error_details = getattr(error, "details", {})
    error_str = str(error).lower()

    is_connection_error = (
        "transport connection lost" in error_str
        or "transport not connected" in error_str
        or "connection closed" in error_str
        or "connection lost" in error_str
    )

    return WorkerInitializationError(
        message=f"Failed to execute command '{command_type}'",
        internal_error=f"ProcessError: {str(error)}",
        stack_trace=traceback.format_exc(),
        context={
            "operation": operation,
            "model_id": model_id,
            "component": "worker_manager",
            "command": command,
            "command_type": command_type,
            "worker_id": worker_id,
            "error_details": error_details,
            "is_connection_error": is_connection_error,
        },
    )


def create_timeout_error(
    model_id: str,
    command: dict,
    timeout: float,
    operation: str,
) -> WorkerInitializationError:
    """
    Create WorkerInitializationError from timeout.

    Args:
        model_id: Model identifier
        command: Command that timed out
        timeout: Timeout value
        operation: Operation name

    Returns:
        WorkerInitializationError with timeout context
    """
    command_type = command.get("command_type", "unknown")

    logger.error(
        f"❌ Command '{command_type}' timed out after {timeout}s for worker {model_id}"
    )

    return WorkerInitializationError(
        message=f"Failed to execute command '{command_type}'",
        internal_error=f"Command '{command_type}' timed out after {timeout}s",
        stack_trace=traceback.format_exc(),
        context={
            "operation": operation,
            "model_id": model_id,
            "component": "worker_manager",
            "command": command,
            "command_type": command_type,
            "timeout": timeout,
        },
    )


def log_connection_error(
    error: Exception,
    model_id: str,
    context: str = "operation",
) -> None:
    """
    Log connection-related errors.

    Args:
        error: Exception instance
        model_id: Model identifier
        context: Operation context
    """
    error_str = str(error).lower()
    is_connection_error = (
        "connection" in error_str
        or "transport" in error_str
        or "disconnect" in error_str
    )

    if is_connection_error:
        # Connection error - log clearly without full traceback
        logger.error(f"❌ Connection error during {context} for {model_id}: {error}")
    else:
        # Unexpected error - log with traceback for debugging
        logger.error(
            f"❌ Unexpected error during {context} for {model_id}: {error}",
            exc_info=True,
        )
