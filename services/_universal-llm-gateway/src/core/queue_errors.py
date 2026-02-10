"""Comprehensive error handling for queue management API

This module provides detailed error handling with specific error codes
for the queue management system.
"""

from enum import Enum
from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger


class QueueErrorCode(Enum):
    """Error codes for queue management operations"""

    INSUFFICIENT_RESOURCES = "insufficient_resources"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_ALREADY_LOADED = "model_already_loaded"
    MODEL_NOT_LOADED = "model_not_loaded"
    MODEL_BUSY = "model_busy"
    LOAD_FAILED = "load_failed"
    UNLOAD_FAILED = "unload_failed"
    INVALID_PRIORITY = "invalid_priority"
    RESOURCE_TRACKING_ERROR = "resource_tracking_error"
    INFERENCE_STATE_ERROR = "inference_state_error"
    TIMEOUT_ERROR = "timeout_error"
    CONCURRENT_OPERATION_ERROR = "concurrent_operation_error"
    SYSTEM_ERROR = "system_error"


class QueueManagementError(Exception):
    """Base exception for queue management errors"""

    def __init__(
        self,
        error_code: QueueErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class InsufficientResourcesError(QueueManagementError):
    """Error when there are insufficient resources to load a model"""

    def __init__(
        self,
        required_vram_mb: int,
        available_vram_mb: int,
        required_ram_mb: int = None,
        available_ram_mb: int = None,
    ):
        message = f"Insufficient resources: need {required_vram_mb}MB VRAM, have {available_vram_mb}MB"
        if required_ram_mb and available_ram_mb:
            message += f"; need {required_ram_mb}MB RAM, have {available_ram_mb}MB"

        super().__init__(
            QueueErrorCode.INSUFFICIENT_RESOURCES,
            message,
            {
                "required_vram_mb": required_vram_mb,
                "available_vram_mb": available_vram_mb,
                "required_ram_mb": required_ram_mb,
                "available_ram_mb": available_ram_mb,
            },
        )


class ModelNotFoundError(QueueManagementError):
    """Error when a model is not found"""

    def __init__(self, model_id: str):
        super().__init__(
            QueueErrorCode.MODEL_NOT_FOUND,
            f"Model {model_id} not found or not enabled",
            {"model_id": model_id},
        )


class ModelAlreadyLoadedError(QueueManagementError):
    """Error when trying to load a model that's already loaded"""

    def __init__(self, model_id: str):
        super().__init__(
            QueueErrorCode.MODEL_ALREADY_LOADED,
            f"Model {model_id} is already loaded",
            {"model_id": model_id},
        )


class ModelNotLoadedError(QueueManagementError):
    """Error when trying to unload a model that's not loaded"""

    def __init__(self, model_id: str):
        super().__init__(
            QueueErrorCode.MODEL_NOT_LOADED,
            f"Model {model_id} is not currently loaded",
            {"model_id": model_id},
        )


class ModelBusyError(QueueManagementError):
    """Error when trying to perform an operation on a busy model"""

    def __init__(self, model_id: str, operation: str = "operation"):
        super().__init__(
            QueueErrorCode.MODEL_BUSY,
            f"Model {model_id} is currently busy and cannot perform {operation}",
            {"model_id": model_id, "operation": operation},
        )


class LoadFailedError(QueueManagementError):
    """Error when model loading fails"""

    def __init__(self, model_id: str, reason: str):
        super().__init__(
            QueueErrorCode.LOAD_FAILED,
            f"Failed to load model {model_id}: {reason}",
            {"model_id": model_id, "reason": reason},
        )


class UnloadFailedError(QueueManagementError):
    """Error when model unloading fails"""

    def __init__(self, model_id: str, reason: str):
        super().__init__(
            QueueErrorCode.UNLOAD_FAILED,
            f"Failed to unload model {model_id}: {reason}",
            {"model_id": model_id, "reason": reason},
        )


class InvalidPriorityError(QueueManagementError):
    """Error when an invalid priority is specified"""

    def __init__(self, priority: str):
        super().__init__(
            QueueErrorCode.INVALID_PRIORITY,
            f"Invalid priority '{priority}'. Must be one of: high, normal, low",
            {"priority": priority},
        )


class TimeoutError(QueueManagementError):
    """Error when an operation times out"""

    def __init__(self, operation: str, timeout_seconds: float):
        super().__init__(
            QueueErrorCode.TIMEOUT_ERROR,
            f"Operation '{operation}' timed out after {timeout_seconds} seconds",
            {"operation": operation, "timeout_seconds": timeout_seconds},
        )


class ConcurrentOperationError(QueueManagementError):
    """Error when a concurrent operation conflicts"""

    def __init__(self, model_id: str, operation: str):
        super().__init__(
            QueueErrorCode.CONCURRENT_OPERATION_ERROR,
            f"Concurrent operation conflict for model {model_id}: {operation}",
            {"model_id": model_id, "operation": operation},
        )


class SystemError(QueueManagementError):
    """Error when a system-level issue occurs"""

    def __init__(self, component: str, reason: str):
        super().__init__(
            QueueErrorCode.SYSTEM_ERROR,
            f"System error in {component}: {reason}",
            {"component": component, "reason": reason},
        )


def handle_queue_error(error: QueueManagementError) -> HTTPException:
    """
    Convert a QueueManagementError to an HTTPException with appropriate status code.

    Args:
        error: The queue management error to convert

    Returns:
        HTTPException with appropriate status code and error details
    """
    # Map error codes to HTTP status codes
    status_code_map = {
        QueueErrorCode.INSUFFICIENT_RESOURCES: 400,
        QueueErrorCode.MODEL_NOT_FOUND: 404,
        QueueErrorCode.MODEL_ALREADY_LOADED: 400,
        QueueErrorCode.MODEL_NOT_LOADED: 404,
        QueueErrorCode.MODEL_BUSY: 409,
        QueueErrorCode.LOAD_FAILED: 500,
        QueueErrorCode.UNLOAD_FAILED: 500,
        QueueErrorCode.INVALID_PRIORITY: 400,
        QueueErrorCode.RESOURCE_TRACKING_ERROR: 500,
        QueueErrorCode.INFERENCE_STATE_ERROR: 500,
        QueueErrorCode.TIMEOUT_ERROR: 408,
        QueueErrorCode.CONCURRENT_OPERATION_ERROR: 409,
        QueueErrorCode.SYSTEM_ERROR: 500,
    }

    status_code = status_code_map.get(error.error_code, 500)

    # Create error response
    error_response = {
        "error": error.error_code.value,
        "message": error.message,
        "details": error.details,
    }

    return HTTPException(status_code=status_code, detail=error_response)


def handle_generic_error(error: Exception, context: str = "Unknown") -> HTTPException:
    """
    Handle generic exceptions and convert them to appropriate HTTP responses.

    Args:
        error: The exception to handle
        context: Context where the error occurred

    Returns:
        HTTPException with appropriate error details
    """
    logger = get_logger(__name__)
    logger.error(f"Generic error in {context}: {error}")

    # Create a system error
    system_error = SystemError(context, str(error))
    return handle_queue_error(system_error)


def validate_priority(priority: str) -> None:
    """
    Validate that a priority value is valid.

    Args:
        priority: Priority value to validate

    Raises:
        InvalidPriorityError: If priority is invalid
    """
    valid_priorities = ["high", "normal", "low"]
    if priority not in valid_priorities:
        raise InvalidPriorityError(priority)


def create_error_response(
    error_code: QueueErrorCode, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Create a standardized error response.

    Args:
        error_code: The error code
        message: Error message
        details: Additional error details

    Returns:
        Dict containing error response
    """
    return {
        "success": False,
        "error": error_code.value,
        "message": message,
        "details": details or {},
        "timestamp": __import__("time").time(),
    }
