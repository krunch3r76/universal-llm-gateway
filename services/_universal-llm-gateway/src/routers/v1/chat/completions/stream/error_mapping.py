"""Error classification and response mapping for streaming errors.

Responsibility: classify runtime errors → (error_code, message) → NDJSON events.
"""

from enum import StrEnum

from universal_logging import get_logger

from src.core.errors import ErrorCode, is_connection_error, is_crash_error

from .ndjson import iter_error_and_complete_events

logger = get_logger(__name__)


class ErrorClassification(StrEnum):
    """Runtime error classification categories."""

    TIMEOUT = "timeout"
    CRASH = "crash"
    CONNECTION = "connection"
    GPU_MEMORY = "gpu_memory"
    GENERIC = "generic"


def classify_runtime_error(error_message: str) -> ErrorClassification:
    """
    Classify RuntimeError by error message content.

    Args:
        error_message: The error message string

    Returns:
        ErrorClassification enum value
    """
    error_lower = error_message.lower()

    if "timed out" in error_lower or "timeout" in error_lower:
        return ErrorClassification.TIMEOUT

    if is_crash_error(error_message):
        return ErrorClassification.CRASH

    if is_connection_error(error_message):
        return ErrorClassification.CONNECTION

    gpu_keywords = ["cuda out of memory", "out of memory", "cuda oom", "gpu memory"]
    if any(kw in error_lower for kw in gpu_keywords):
        return ErrorClassification.GPU_MEMORY

    return ErrorClassification.GENERIC


def _format_request_context(request_id: str | None) -> str:
    """Format request ID suffix for error messages."""
    return f" Request ID: {request_id}" if request_id else ""


def iter_timeout_error_events(
    error_str: str,
    request_id: str | None = None,
):
    """
    Map TimeoutError to NDJSON error events.

    Args:
        error_str: Timeout error string
        request_id: Optional request tracking ID

    Yields:
        str: NDJSON error event, then completion event
    """
    message = f"Request timed out - {error_str}" if error_str else "Request timed out"
    message += _format_request_context(request_id)
    yield from iter_error_and_complete_events(
        message, "server_error", ErrorCode.REQUEST_TIMEOUT
    )


def iter_runtime_error_events(
    error: RuntimeError,
    model_id: str,
    request_id: str | None = None,
):
    """
    Map RuntimeError to NDJSON error events.

    Classifies error and yields appropriate NDJSON events.

    Args:
        error: The RuntimeError exception
        model_id: Model identifier
        request_id: Optional request tracking ID

    Yields:
        str: NDJSON error event, then completion event
    """
    error_message = str(error)
    classification = classify_runtime_error(error_message)
    request_suffix = _format_request_context(request_id)

    match classification:
        case ErrorClassification.TIMEOUT:
            message = f"Request timed out - {error_message}{request_suffix}"
            yield from iter_error_and_complete_events(
                message, "server_error", ErrorCode.REQUEST_TIMEOUT
            )

        case ErrorClassification.CRASH:
            message = f"Model {model_id} crashed: {error_message}{request_suffix}"
            yield from iter_error_and_complete_events(
                message, "server_error", "model_crashed"
            )

        case ErrorClassification.CONNECTION:
            error_lower = error_message.lower()
            if (
                "connection closed by peer" in error_lower
                or "transport error" in error_lower
            ):
                message = "Model process crashed - likely VRAM OOM"
                code = ErrorCode.GPU_MEMORY_ERROR
            else:
                message = "Model process connection lost"
                code = ErrorCode.PROCESS_CONNECTION_LOST
            message += request_suffix
            message += " Suggestion: Try reducing max_tokens or context length"
            yield from iter_error_and_complete_events(message, "server_error", code)

        case ErrorClassification.GPU_MEMORY:
            message = f"GPU memory exhausted{request_suffix}"
            message += " Suggestion: Try reducing max_tokens or use smaller model"
            yield from iter_error_and_complete_events(
                message, "server_error", ErrorCode.GPU_MEMORY_ERROR
            )

        case ErrorClassification.GENERIC:
            message = f"Model inference failed: {error_message}{request_suffix}"
            yield from iter_error_and_complete_events(
                message, "server_error", ErrorCode.MODEL_ERROR
            )


def iter_process_error_events(
    error: Exception,
    model_id: str,
    request_id: str | None = None,
):
    """
    Map ProcessError to NDJSON error events.

    Args:
        error: The ProcessError exception
        model_id: Model identifier
        request_id: Optional request tracking ID

    Yields:
        str: NDJSON error event, then completion event
    """
    error_message = str(error)
    request_suffix = _format_request_context(request_id)

    if "STREAM_LIMIT_EXCEEDED" in error_message:
        message = f"Worker stream limit exceeded for {model_id}"
        message += request_suffix
        logger.error(f"❌ ProcessError in streaming: {message}")
        # Use canonical STREAM_LIMIT_EXCEEDED code (from universal_protocol)
        # for retryable capacity errors. Gateway's local ErrorCode class
        # doesn't include this, but Stargate checks against universal_protocol.
        yield from iter_error_and_complete_events(
            message, "server_error", "STREAM_LIMIT_EXCEEDED"
        )
    else:
        message = f"Process error for {model_id}: {error_message}"
        message += request_suffix
        logger.error(f"❌ ProcessError in streaming: {message}")
        yield from iter_error_and_complete_events(
            message, "server_error", ErrorCode.MODEL_ERROR
        )
