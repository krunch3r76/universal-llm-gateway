"""OpenAI-compliant error response builders."""

from fastapi.responses import JSONResponse

# Valid OpenAI API error types (specification-compliant)
VALID_ERROR_TYPES = frozenset(
    [
        "invalid_request_error",
        "authentication_error",
        "rate_limit_error",
        "server_error",
        "permission_error",
    ]
)


def validate_openai_error_type(error_type: str) -> str:
    """
    Validate error_type against OpenAI API specification.

    Args:
        error_type: Error type to validate

    Returns:
        str: Validated error type (defaults to server_error if invalid)
    """
    if error_type not in VALID_ERROR_TYPES:
        return "server_error"
    return error_type


def build_openai_error_payload(
    message: str,
    error_type: str,
    error_code: str | None = None,
    param: str | None = None,
) -> dict:
    """
    Build OpenAI error payload structure.

    Args:
        message: Error message
        error_type: One of VALID_ERROR_TYPES
        error_code: Optional error code
        param: Optional parameter name

    Returns:
        dict: OpenAI-compliant error structure
    """
    error_obj = {
        "message": message,
        "type": error_type,
    }
    if error_code is not None:
        error_obj["code"] = error_code
    if param is not None:
        error_obj["param"] = param
    return {"error": error_obj}


def create_openai_error_response(
    status_code: int,
    message: str,
    error_type: str,
    error_code: str | None = None,
    param: str | None = None,
    request_id: str | None = None,
    duration_ms: float | None = None,
    suggestion: str | None = None,
) -> JSONResponse:
    """
    Create OpenAI API error response.

    Args:
        status_code: HTTP status code
        message: Error message
        error_type: Error type (see VALID_ERROR_TYPES)
        error_code: Optional error code
        param: Optional parameter name
        request_id: Optional request ID (appended to message)
        duration_ms: Optional duration (appended to message)
        suggestion: Optional suggestion (appended to message)

    Returns:
        JSONResponse: OpenAI-compliant error response
    """
    validated_type = validate_openai_error_type(error_type)

    # Augment message with optional context
    full_message = message
    if suggestion:
        full_message += f" Suggestion: {suggestion}"
    if request_id:
        full_message += f" Request ID: {request_id}"
    if duration_ms is not None:
        full_message += f" Duration: {duration_ms:.2f}ms"

    payload = build_openai_error_payload(
        full_message, validated_type, error_code, param
    )
    return JSONResponse(status_code=status_code, content=payload)


def create_model_crash_error_response(
    model_id: str,
    error_message: str,
    request_id: str | None = None,
    duration_ms: float | None = None,
) -> JSONResponse:
    """
    Create error response for model crash scenarios.

    Args:
        model_id: Model that crashed
        error_message: Crash error message
        request_id: Optional request ID
        duration_ms: Optional request duration

    Returns:
        JSONResponse: OpenAI-compliant error response (503)
    """
    message = f"Model {model_id} crashed during inference: {error_message}"
    return create_openai_error_response(
        status_code=503,
        message=message,
        error_type="server_error",
        error_code="model_crashed",
        param="model",
        request_id=request_id,
        duration_ms=duration_ms,
    )
