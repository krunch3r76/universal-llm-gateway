"""Map worker/runtime failures to OpenAI-compatible HTTP responses."""

from __future__ import annotations

from universal_logging import get_logger

from src.core.errors import (
    ErrorCode,
    is_connection_error,
    is_crash_error,
    is_variant_sm_error,
)

from .openai_errors import (
    create_model_crash_error_response,
    create_openai_error_response,
)

logger = get_logger(__name__)


def handle_runtime_error(
    e: RuntimeError,
    model_id: str,
    request_id: str,
    response_time_ms: float,
):
    """Handle RuntimeError with appropriate response type."""
    error_message = str(e)

    if "timed out" in error_message.lower() or "timeout" in error_message.lower():
        logger.error("Request timeout for %s: %s", model_id, error_message)
        return create_openai_error_response(
            status_code=504,
            message="Request timed out",
            error_type="server_error",
            error_code=ErrorCode.REQUEST_TIMEOUT,
            request_id=request_id,
            duration_ms=response_time_ms,
        )

    if is_crash_error(error_message):
        logger.error("Model crash for %s: %s", model_id, error_message)
        return create_model_crash_error_response(
            model_id, error_message, request_id, response_time_ms
        )

    if is_variant_sm_error(error_message):
        logger.warning("Variant SM refusal for %s: %s", model_id, error_message)
        return create_openai_error_response(
            status_code=503,
            message=f"Model temporarily unavailable: {error_message}",
            error_type="server_error",
            error_code=ErrorCode.RESOURCE_UNAVAILABLE,
            request_id=request_id,
            duration_ms=response_time_ms,
        )

    if is_connection_error(error_message):
        suggestion = "Try reducing max_tokens or context length"
        if (
            "connection closed by peer" in error_message.lower()
            or "transport error" in error_message.lower()
        ):
            message = "Model process crashed - likely VRAM OOM"
            error_code = ErrorCode.GPU_MEMORY_ERROR
        else:
            message = "Model process connection lost"
            error_code = ErrorCode.PROCESS_CONNECTION_LOST
        return create_openai_error_response(
            status_code=503,
            message=message,
            error_type="server_error",
            error_code=error_code,
            request_id=request_id,
            duration_ms=response_time_ms,
            suggestion=suggestion,
        )

    logger.error("Model runtime error for %s: %s", model_id, error_message)
    return create_openai_error_response(
        status_code=500,
        message=f"Model inference failed: {error_message}",
        error_type="server_error",
        error_code=ErrorCode.MODEL_ERROR,
        param="model",
        request_id=request_id,
        duration_ms=response_time_ms,
    )
