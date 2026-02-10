"""
Error types and status code mapping.

Provides ErrorFormat enum and helper methods for determining error types
and status codes from exceptions.
"""

from enum import Enum


class ErrorFormat(str, Enum):
    """Supported error response formats"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    CUSTOM = "custom"


def determine_error_type(status_code: int, exc_type: str | None = None) -> str:
    """
    Map HTTP status code to OpenAI error type.

    Args:
        status_code: HTTP status code
        exc_type: Optional exception type name for additional context

    Returns:
        str: OpenAI error type
    """
    # Authentication errors
    if status_code in (401, 403):
        return "authentication_error"

    # Rate limiting
    if status_code == 429:
        return "rate_limit_exceeded"

    # Client errors (400-499)
    if 400 <= status_code < 500:
        return "invalid_request_error"

    # Service unavailable
    if status_code == 503:
        return "service_unavailable"

    # Gateway timeout
    if status_code == 504:
        return "timeout"

    # Server errors (500-599)
    if 500 <= status_code < 600:
        return "api_error"

    # Default
    return "api_error"


def determine_status_code(exc: Exception, default: int = 500) -> int:
    """
    Determine appropriate HTTP status code from exception type.

    Args:
        exc: Python exception
        default: Default status code if not determinable

    Returns:
        int: HTTP status code
    """
    # Check if exception has status_code attribute (e.g., HTTPException)
    if hasattr(exc, "status_code"):
        return exc.status_code

    # Map exception types to status codes
    if isinstance(exc, ValueError | TypeError | KeyError):
        return 400  # Bad Request

    if isinstance(exc, FileNotFoundError):
        return 404  # Not Found

    if isinstance(exc, PermissionError):
        return 403  # Forbidden

    if isinstance(exc, TimeoutError):
        return 504  # Gateway Timeout

    if isinstance(exc, ConnectionError):
        return 503  # Service Unavailable

    # Default to provided default (usually 500)
    return default
