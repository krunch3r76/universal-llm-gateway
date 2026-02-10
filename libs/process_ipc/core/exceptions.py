"""
Custom exceptions for process-ipc package.

Contains all the custom exception classes used throughout the IPC system
for proper error handling and debugging.
"""

from typing import Any


class ProcessIPCError(Exception):
    """
    Base exception for all process-ipc errors.

    All other exceptions in this package inherit from this base class.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}. Details: {self.details}"
        return self.message


class ConnectionError(ProcessIPCError):
    """
    Exception raised when connection operations fail.

    Raised when transport connections cannot be established,
    are lost unexpectedly, or encounter other connection-related issues.
    """

    def __init__(
        self,
        message: str,
        address: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.address = address

    def __str__(self) -> str:
        if self.address:
            return f"Connection error at {self.address}: {self.message}"
        return f"Connection error: {self.message}"


class TimeoutError(ProcessIPCError):
    """
    Exception raised when operations exceed their timeout limits.

    Raised when any operation (connection, send, receive, etc.)
    takes longer than the specified timeout duration.
    """

    def __init__(
        self,
        message: str,
        timeout: float | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.timeout = timeout

    def __str__(self) -> str:
        if self.timeout:
            return f"Operation timed out after {self.timeout}s: {self.message}"
        return f"Operation timed out: {self.message}"


class ProcessError(ProcessIPCError):
    """
    Exception raised when process operations fail.

    Raised when process startup, shutdown, or management operations
    encounter errors or unexpected conditions.
    """

    def __init__(
        self,
        message: str,
        process_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.process_id = process_id

    def __str__(self) -> str:
        if self.process_id:
            return f"Process error for {self.process_id}: {self.message}"
        return f"Process error: {self.message}"


class TransportError(ProcessIPCError):
    """
    Exception raised when transport operations fail.

    Raised when low-level transport operations (send, receive, etc.)
    encounter errors that are not connection-related.
    """

    def __init__(
        self,
        message: str,
        transport_type: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.transport_type = transport_type

    def __str__(self) -> str:
        if self.transport_type:
            return f"Transport error ({self.transport_type}): {self.message}"
        return f"Transport error: {self.message}"


class ConfigurationError(ProcessIPCError):
    """
    Exception raised when configuration is invalid.

    Raised when configuration parameters are missing, invalid,
    or inconsistent.
    """

    def __init__(
        self,
        message: str,
        config_key: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.config_key = config_key

    def __str__(self) -> str:
        if self.config_key:
            return f"Configuration error for {self.config_key}: {self.message}"
        return f"Configuration error: {self.message}"


class MessageError(ProcessIPCError):
    """
    Exception raised when message processing fails.

    Raised when message serialization, deserialization, or
    processing encounters errors.
    """

    def __init__(
        self,
        message: str,
        message_type: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.message_type = message_type

    def __str__(self) -> str:
        if self.message_type:
            return f"Message error ({self.message_type}): {self.message}"
        return f"Message error: {self.message}"


class WorkerError(ProcessIPCError):
    """
    Exception raised by worker processes.

    Raised when worker processes encounter errors during
    initialization, message processing, or other operations.
    """

    def __init__(
        self,
        message: str,
        worker_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.worker_id = worker_id

    def __str__(self) -> str:
        if self.worker_id:
            return f"Worker error for {self.worker_id}: {self.message}"
        return f"Worker error: {self.message}"


class ResourceError(ProcessIPCError):
    """
    Exception raised when system resources are unavailable.

    Raised when the system lacks sufficient resources (memory, file descriptors, etc.)
    to complete the requested operation.
    """

    def __init__(
        self,
        message: str,
        resource_type: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.resource_type = resource_type

    def __str__(self) -> str:
        if self.resource_type:
            return f"Resource error ({self.resource_type}): {self.message}"
        return f"Resource error: {self.message}"


class ProcessRecoveryError(ProcessIPCError):
    """
    Exception raised when process recovery fails.

    Raised when automatic process recovery operations fail
    after exhausting all recovery attempts.
    """

    def __init__(
        self,
        message: str,
        process_id: str | None = None,
        recovery_attempts: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.process_id = process_id
        self.recovery_attempts = recovery_attempts

    def __str__(self) -> str:
        if self.process_id:
            return f"Process recovery error for {self.process_id}: {self.message}"
        return f"Process recovery error: {self.message}"


class ProcessHealthError(ProcessIPCError):
    """
    Exception raised when health check fails.

    Raised when process health monitoring operations fail
    or detect critical health issues.
    """

    def __init__(
        self,
        message: str,
        process_id: str | None = None,
        health_status: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.process_id = process_id
        self.health_status = health_status

    def __str__(self) -> str:
        if self.process_id:
            return f"Process health error for {self.process_id}: {self.message}"
        return f"Process health error: {self.message}"
