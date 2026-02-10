"""
Centralized error patterns and constants for consistent error handling.

This module defines error patterns, types, and codes used throughout the gateway
to ensure consistent error classification and response formatting.
"""

import traceback
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException


# Error types following OpenAI API conventions
class ErrorType:
    """OpenAI-compatible error types."""

    CONNECTION_ERROR = "connection_error"
    TIMEOUT_ERROR = "timeout_error"
    SERVICE_UNAVAILABLE_ERROR = "service_unavailable_error"
    INTERNAL_SERVER_ERROR = "internal_server_error"
    INVALID_REQUEST_ERROR = "invalid_request_error"
    SYNTAX_ERROR = "syntax_error"
    INITIALIZATION_ERROR = "initialization_error"
    RESOURCE_ERROR = "resource_error"
    CONFIGURATION_ERROR = "configuration_error"
    NETWORK_ERROR = "network_error"
    UNKNOWN_ERROR = "unknown_error"


# Error codes for specific error conditions
class ErrorCode:
    """Specific error codes for different failure scenarios."""

    PROCESS_CONNECTION_LOST = "process_connection_lost"
    REQUEST_TIMEOUT = "request_timeout"
    MODEL_LOADING_FAILED = "model_loading_failed"
    MODEL_RECOVERY_FAILED = "model_recovery_failed"
    MODEL_ERROR = "model_error"
    GPU_MEMORY_ERROR = "gpu_memory_error"
    UNEXPECTED_ERROR = "unexpected_error"
    SYNTAX_ERROR = "syntax_error"
    WORKER_INITIALIZATION_FAILED = "worker_initialization_failed"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    CONFIGURATION_ERROR = "configuration_error"
    MODEL_CRASHED = "model_crashed"  # Specific code for worker crashes
    INPUT_FORMAT_MISMATCH = (
        "input_format_mismatch"  # Client provided wrong format for model
    )
    INVALID_MODEL_CONFIGURATION = (
        "invalid_model_configuration"  # Model has invalid input_schema
    )
    MODEL_METADATA_MISSING = "model_metadata_missing"  # Model metadata not found
    COMPUTE_CAPACITY = "compute_capacity"  # Compute capacity exceeded


# Patterns to identify connection/transport errors
# These are matched against error message strings (case-insensitive)
CONNECTION_ERROR_PATTERNS = [
    "connection closed by peer",
    "transport error",
    "not connected",
    "broken pipe",
    "connection reset",
    "network unreachable",
    "connection refused",
    "connection timeout",
    "socket error",
    "transport disconnected",
]

# NEW: Patterns to identify worker crash errors (fallback only)
# Primary detection should use direct process_ipc status information
CRASH_ERROR_PATTERNS = [
    "worker crashed",
    "model crashed",
    "worker process died",
    "worker process crashed",  # From controller when connection error occurs
    "process terminated unexpectedly",
    "worker process terminated",
    "process died unexpectedly",
    "process is dead",  # From health monitor logs
    "exit code: -6",  # SIGABRT signal
    "exit code: -9",  # SIGKILL signal
    "exit code: -11",  # SIGSEGV signal
]


def is_connection_error(error_message: str) -> bool:
    """
    Check if an error message indicates a connection/transport error.

    Args:
        error_message: Error message to check

    Returns:
        True if the error is a connection-related error
    """
    if not error_message:
        return False

    error_lower = error_message.lower()
    return any(pattern in error_lower for pattern in CONNECTION_ERROR_PATTERNS)


# NEW: Add crash error detection function
def is_crash_error(error_message: str) -> bool:
    """
    Check if an error message indicates a worker crash.

    Args:
        error_message: Error message to check

    Returns:
        True if the error is a crash-related error
    """
    if not error_message:
        return False

    error_lower = error_message.lower()
    return any(pattern in error_lower for pattern in CRASH_ERROR_PATTERNS)


# Manual crash detection functions removed - process_ipc handles this automatically


def classify_error(error_message: str) -> tuple[str, str]:
    """
    Classify an error message and return appropriate error type and code.

    This function prioritizes direct process_ipc status detection over pattern matching.
    Pattern matching is only used as a fallback for edge cases.

    Args:
        error_message: Error message to classify

    Returns:
        Tuple of (error_type, error_code)
    """
    if not error_message:
        return ErrorType.INTERNAL_SERVER_ERROR, ErrorCode.UNEXPECTED_ERROR

    # Primary detection: Check for direct process_ipc status information
    if (
        "ProcessStatus.STOPPED" in error_message
        or "ProcessStatus.ERROR" in error_message
    ):
        return ErrorType.SERVICE_UNAVAILABLE_ERROR, ErrorCode.MODEL_CRASHED

    # Secondary detection: Check for crash patterns (fallback)
    if is_crash_error(error_message):
        return ErrorType.SERVICE_UNAVAILABLE_ERROR, ErrorCode.MODEL_CRASHED

    # Check for connection errors
    if is_connection_error(error_message):
        return ErrorType.CONNECTION_ERROR, ErrorCode.PROCESS_CONNECTION_LOST

    # Default to internal server error
    return ErrorType.INTERNAL_SERVER_ERROR, ErrorCode.UNEXPECTED_ERROR


class GatewayError(Exception):
    """Base class for gateway errors with structured details."""

    def __init__(
        self,
        message: str,
        error_type: str,
        error_code: str = None,
        details: dict[str, Any] | None = None,
        internal_error: str | None = None,
        stack_trace: str | None = None,
        context: dict[str, Any] | None = None,
    ):
        """
        Initialize gateway error with structured information.

        Args:
            message: Human-readable error message
            error_type: Error category (syntax_error, initialization_error, etc.)
            error_code: Specific error code
            details: Additional error details
            internal_error: Raw internal error message
            stack_trace: Full stack trace
            context: Context information (operation, model_id, etc.)
        """
        self.message = message
        self.error_type = error_type
        self.error_code = error_code or error_type.upper()
        self.details = details or {}

        # Capture internal error details
        if internal_error:
            self.details["internal_error"] = internal_error

        # Capture stack trace if provided
        if stack_trace:
            self.details["stack_trace"] = stack_trace

        # Add context information
        if context:
            self.details["context"] = context

        super().__init__(message)


class WorkerInitializationError(GatewayError):
    """Error during worker initialization."""

    def __init__(
        self,
        message: str,
        error_type: str = ErrorType.INITIALIZATION_ERROR,
        error_code: str = ErrorCode.WORKER_INITIALIZATION_FAILED,
        **kwargs,
    ):
        super().__init__(
            message=message, error_type=error_type, error_code=error_code, **kwargs
        )


class ModelLoadingError(GatewayError):
    """Error during model loading."""

    def __init__(
        self,
        message: str,
        error_type: str = ErrorType.INITIALIZATION_ERROR,
        error_code: str = ErrorCode.MODEL_LOADING_FAILED,
        **kwargs,
    ):
        super().__init__(
            message=message, error_type=error_type, error_code=error_code, **kwargs
        )


class SyntaxErrorException(GatewayError):
    """Python syntax error in code."""

    def __init__(
        self,
        message: str,
        error_type: str = ErrorType.SYNTAX_ERROR,
        error_code: str = ErrorCode.SYNTAX_ERROR,
        **kwargs,
    ):
        super().__init__(
            message=message, error_type=error_type, error_code=error_code, **kwargs
        )


def create_error_response(
    error: Exception,
    status_code: int = 500,
    context: dict[str, Any] | None = None,
    include_stack_trace: bool = True,
    include_internal_details: bool = True,
) -> HTTPException:
    """
    Create structured error response for HTTP endpoints.

    Args:
        error: Exception that occurred
        status_code: HTTP status code
        context: Context information (operation, model_id, etc.)
        include_stack_trace: Whether to include stack trace
        include_internal_details: Whether to include internal error details

    Returns:
        HTTPException with structured error details
    """
    context = context or {}

    if isinstance(error, GatewayError):
        # Use structured error details from GatewayError
        error_details = {
            "internal_error": error.details.get("internal_error", str(error)),
            "context": {
                "operation": context.get("operation", "unknown"),
                "model_id": context.get("model_id"),
                "component": context.get("component", "unknown"),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "request_id": context.get("request_id", str(uuid.uuid4())[:8]),
            },
        }

        # Add stack trace if available and enabled
        if include_stack_trace and error.details.get("stack_trace"):
            error_details["stack_trace"] = error.details["stack_trace"]

        # Merge additional context from error
        if error.details.get("context"):
            error_details["context"].update(error.details["context"])

        # Add any other details from the error
        for key, value in error.details.items():
            if key not in ["internal_error", "stack_trace", "context"]:
                error_details[key] = value

        return HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "message": error.message,
                    "type": error.error_type,
                    "code": error.error_code,
                    "details": error_details if include_internal_details else None,
                }
            },
        )
    else:
        # Generic error handling for non-GatewayError exceptions
        error_details = {
            "internal_error": str(error),
            "context": {
                "operation": context.get("operation", "unknown"),
                "component": context.get("component", "unknown"),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "request_id": context.get("request_id", str(uuid.uuid4())[:8]),
            },
        }

        # Add stack trace if enabled
        if include_stack_trace:
            error_details["stack_trace"] = traceback.format_exc()

        return HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "message": f"Internal gateway error: {str(error)}",
                    "type": ErrorType.UNKNOWN_ERROR,
                    "code": ErrorCode.UNEXPECTED_ERROR,
                    "details": error_details if include_internal_details else None,
                }
            },
        )
