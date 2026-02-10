"""Gateway error parsing for federation inference.

Provides consistent error parsing from Gateway HTTP responses.
Handles multiple error shapes: canonical envelope, FastAPI detail, OpenAI error.
"""

import httpx
from fastapi import HTTPException
from universal_logging import get_logger
from universal_protocol import ErrorCode, ErrorSource, error_envelope

logger = get_logger(__name__)


def parse_gateway_error(response: httpx.Response) -> dict:
    """Parse error dict from Gateway HTTP response.

    Handles multiple error response shapes:
    1. Canonical envelope: {"code": "...", "message": "...", "source": "..."}
    2. FastAPI detail (string): {"detail": "error message"}
    3. FastAPI detail (dict): {"detail": {"message": "...", ...}}
    4. OpenAI error: {"error": {"message": "...", "type": "...", "code": "..."}}
    5. Fallback: raw response text

    Args:
        response: HTTP response with error status

    Returns:
        Normalized error dict with at least 'message' key
    """
    try:
        payload = response.json()
    except Exception:
        # JSON parsing failed - return raw text
        return {
            "message": f"Gateway error: {response.status_code}",
            "raw": response.text[:500] if response.text else None,
        }

    if not isinstance(payload, dict):
        return {
            "message": f"Gateway error: {response.status_code}",
            "raw": str(payload)[:500],
        }

    # Case 1: Canonical envelope (has 'message' at top level)
    if "message" in payload and isinstance(payload.get("message"), str):
        return payload

    # Case 2: FastAPI detail
    if "detail" in payload:
        detail = payload["detail"]
        if isinstance(detail, str):
            return {"message": detail}
        if isinstance(detail, dict):
            # detail might be a canonical envelope or arbitrary dict
            if "message" in detail:
                return detail
            # Arbitrary dict - stringify
            return {"message": str(detail), "detail": detail}
        # List or other type
        return {"message": str(detail)}

    # Case 3: OpenAI error format
    if "error" in payload and isinstance(payload.get("error"), dict):
        error_obj = payload["error"]
        return {
            "message": error_obj.get("message", "Gateway error"),
            "code": error_obj.get("code"),
            "type": error_obj.get("type"),
            "param": error_obj.get("param"),
            "openai_error": error_obj,  # Preserve full error for debugging
        }

    # Case 4: Has 'code' but no 'message' - partial envelope
    if "code" in payload:
        return {
            "message": f"Gateway error: {payload.get('code')}",
            "code": payload.get("code"),
        }

    # Fallback: return entire payload
    return {"message": f"Gateway error: {response.status_code}", "raw": payload}


def create_gateway_http_exception(
    error: httpx.HTTPStatusError,
    source: ErrorSource = "edge",
) -> HTTPException:
    """Create HTTPException from Gateway HTTP error.

    Args:
        error: The httpx.HTTPStatusError from Gateway
        source: Error source layer (default "edge" as forwarder)

    Returns:
        HTTPException with canonical error envelope
    """
    gateway_error = parse_gateway_error(error.response)
    status_code = error.response.status_code

    # Extract code from parsed error, or infer from HTTP status
    code = gateway_error.get("code")
    if code is None:
        if status_code == 503:
            code = ErrorCode.GATEWAY_AT_CAPACITY
        elif status_code == 404:
            code = ErrorCode.MODEL_NOT_FOUND
        else:
            # Non-capacity failures must NOT poison routing/circuit state
            code = ErrorCode.UNEXPECTED_ERROR

    message = gateway_error.get("message", f"Gateway error: {status_code}")

    # Prefer upstream retryable field (preserves structured semantics),
    # fall back to status-based heuristic
    upstream_retryable = gateway_error.get("retryable")
    if isinstance(upstream_retryable, bool):
        retryable = upstream_retryable
    else:
        retryable = status_code == 503

    return HTTPException(
        status_code=status_code,
        detail=error_envelope(
            code=code,
            message=message,
            source=source,
            retryable=retryable,
            data={"gateway_error": gateway_error},
        ),
    )
