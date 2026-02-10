"""
Core components for process-ipc package.

Contains abstract interfaces, type definitions, and exception classes
that form the foundation of the IPC system.
"""

# New message structure modules
from . import signals
from .exceptions import (
    ConnectionError,
    ProcessError,
    ProcessIPCError,
    TimeoutError,
    TransportError,
)
from .interfaces import WorkerInterface
from .messages import (
    MessageStructure,
    create_message,
    extract_from_payload,
    generate_correlation_id,
    generate_message_id,
    get_correlation_id,
    get_worker_id,
    validate_message,
)
from .messages import (
    ValidationError as MessageValidationError,
)
from .types import ProcessHealth, ProcessStatus

__all__ = [
    "WorkerInterface",
    "ProcessStatus",
    "ProcessHealth",
    "ProcessIPCError",
    "ConnectionError",
    "TimeoutError",
    "ProcessError",
    "TransportError",
    # Signals (module)
    "signals",
    # Message utilities
    "create_message",
    "validate_message",
    "extract_from_payload",
    "get_worker_id",
    "get_correlation_id",
    "generate_message_id",
    "generate_correlation_id",
    "MessageStructure",
    "MessageValidationError",
]
