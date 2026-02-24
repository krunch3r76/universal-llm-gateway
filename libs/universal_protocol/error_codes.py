"""Canonical error codes for Universal Protocol.

Error Code Naming: SCREAMING_SNAKE
Error codes are stable identifiers for programmatic handling.

Categories:
- Capacity: retryable=True, status=503
- Connectivity: retryable=True, status=503
- Timeout: retryable=True, status=504 (CAPACITY_TIMEOUT: 503)
- Client: retryable=False, status=4xx
- Internal: retryable=False, status=5xx
"""

from enum import StrEnum

from universal_logging import get_logger

logger = get_logger(__name__)


class ErrorCode(StrEnum):
    """Canonical error codes for federation and inference."""

    # Capacity errors (503, retryable)
    STICKY_CAPACITY = "STICKY_CAPACITY"
    STREAM_LIMIT_EXCEEDED = "STREAM_LIMIT_EXCEEDED"
    GATEWAY_AT_CAPACITY = "GATEWAY_AT_CAPACITY"
    EVICTION_FAILED = "EVICTION_FAILED"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    NO_FEASIBLE_GATEWAY = "NO_FEASIBLE_GATEWAY"

    # Connectivity errors (503, retryable with backoff)
    GATEWAY_DISCONNECTED = "GATEWAY_DISCONNECTED"
    EDGE_UNREACHABLE = "EDGE_UNREACHABLE"

    # Timeout errors (504, retryable with backoff)
    CAPACITY_TIMEOUT = "CAPACITY_TIMEOUT"
    INFERENCE_TIMEOUT = "INFERENCE_TIMEOUT"
    LOAD_TIMEOUT = "LOAD_TIMEOUT"
    REQUEST_TIMEOUT = "REQUEST_TIMEOUT"

    # Client errors (4xx, not retryable)
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"

    # Engine errors (5xx, varies)
    OOM = "OOM"
    CUDA_ERROR = "CUDA_ERROR"

    # Internal errors (5xx, not retryable)
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


# Mapping: code → (retryable, http_status)
ERROR_METADATA: dict[ErrorCode, tuple[bool, int]] = {
    ErrorCode.STICKY_CAPACITY: (True, 503),
    ErrorCode.STREAM_LIMIT_EXCEEDED: (True, 503),
    ErrorCode.GATEWAY_AT_CAPACITY: (True, 503),
    ErrorCode.EVICTION_FAILED: (True, 503),
    ErrorCode.RESOURCE_UNAVAILABLE: (True, 503),
    ErrorCode.NO_FEASIBLE_GATEWAY: (True, 503),
    ErrorCode.GATEWAY_DISCONNECTED: (True, 503),
    ErrorCode.EDGE_UNREACHABLE: (True, 503),
    ErrorCode.CAPACITY_TIMEOUT: (True, 503),
    ErrorCode.INFERENCE_TIMEOUT: (True, 504),
    ErrorCode.LOAD_TIMEOUT: (True, 504),
    ErrorCode.REQUEST_TIMEOUT: (True, 504),
    ErrorCode.MODEL_NOT_FOUND: (False, 404),
    ErrorCode.INVALID_REQUEST: (False, 400),
    ErrorCode.OOM: (False, 503),
    ErrorCode.CUDA_ERROR: (False, 503),
    ErrorCode.UNEXPECTED_ERROR: (False, 500),
    ErrorCode.CONFIGURATION_ERROR: (False, 500),
}


def is_retryable(code: str) -> bool:
    """Check if error code indicates retryable error.

    Args:
        code: Error code string to check

    Returns:
        True if error is retryable, False otherwise

    Note:
        Unknown codes log ERROR and return False (per Defaults Policy).
    """
    try:
        error_code = ErrorCode(code)
        return ERROR_METADATA.get(error_code, (False, 500))[0]
    except ValueError:
        logger.error(f"Unknown error code '{code}', defaulting retryable=False")
        return False


def get_http_status(code: str) -> int:
    """Get HTTP status for error code.

    Args:
        code: Error code string to look up

    Returns:
        HTTP status code (e.g., 503, 504, 404)

    Note:
        Unknown codes log ERROR and return 500 (per Defaults Policy).
    """
    try:
        error_code = ErrorCode(code)
        return ERROR_METADATA.get(error_code, (False, 500))[1]
    except ValueError:
        logger.error(f"Unknown error code '{code}', defaulting status=500")
        return 500
