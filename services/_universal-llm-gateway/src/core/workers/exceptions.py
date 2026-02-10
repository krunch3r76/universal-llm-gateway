"""
Worker-specific exceptions.

This module defines custom exceptions used throughout the workers module
for better error handling and debugging.
"""


class WorkerError(Exception):
    """Base exception for worker-related errors."""

    pass


class WorkerStartupError(WorkerError):
    """Raised when a worker fails to start."""

    pass


class WorkerInitializationError(WorkerError):
    """Raised when a worker fails to initialize."""

    pass


class ModelLoadingError(WorkerError):
    """Raised when a model fails to load in a worker."""

    pass


class WorkerCommunicationError(WorkerError):
    """Raised when communication with a worker fails."""

    pass


class WorkerTimeoutError(WorkerError):
    """Raised when a worker operation times out."""

    pass


class WorkerHealthError(WorkerError):
    """Raised when a worker health check fails."""

    pass


class WorkerShutdownError(WorkerError):
    """Raised when a worker fails to shutdown properly."""

    pass


class ModelNotFoundError(WorkerError):
    """Raised when a requested model is not found."""

    pass


class UnsupportedModelFormatError(WorkerError):
    """Raised when an unsupported model format is requested."""

    pass


class GPUMemoryError(WorkerError):
    """Raised when GPU memory is exhausted."""

    pass
