"""
Process IPC Package

A simplified inter-process communication (IPC) system for single worker process management.

Features:
- Single worker process management with ProcessSupervisor
- Health monitoring with automatic recovery (enabled by default)
- Resource monitoring (RAM/VRAM) with background collection (enabled by default)
- Structured logging with universal_logging integration
- Unix socket transport layer with Protocol Buffer serialization (default)
- State machine for supervisor lifecycle management
- Event-driven architecture with MessagePump

LOGGING SETUP:
Users must explicitly configure logging to prevent console spam.

Example:
```python
# from process_ipc import setup_logging  # REMOVED - use universal_logging directly
setup_logging()  # Uses internal config automatically

# Or with custom config
setup_logging(config_file="config/logging.yaml")
```
"""

# Core types and interfaces
# Transport layer (now using universal_transport)
# Utilities
# from .services.logging import get_logger  # REMOVED - use universal_logging directly
from universal_logging import get_logger
from universal_transport import (
    AsyncUnixServer,
    AsyncUnixTransport,
    ProcessIPCCompatibleClient,
    ProcessIPCCompatibleServer,
)
from universal_transport.core.interfaces import Transport
from universal_transport.core.message_pump import MessagePump

# Transport interface already imported above (line 30)
# Configuration
from .core.config import (
    ProcessHealthConfig,
    ResourceMonitoringConfig,
    SupervisorConfig,
    UnixSocketConfig,
)
from .core.config_validators import (
    validate_choice,
    validate_minimum,
    validate_non_negative,
    validate_positive,
    validate_string_not_empty,
)
from .core.exceptions import (
    ConnectionError,
    ProcessError,
    ProcessHealthError,
    ProcessIPCError,
    ProcessRecoveryError,
    TransportError,
)
from .core.interfaces import EventBusProtocol, WorkerInterface
from .core.schemas import (
    CommandPayload,
    DomainResponse,
    ErrorResponse,
    InferenceRequest,
    InferenceResponse,
    IPCMessage,
    PingRequest,
    PingResponse,
    create_command_payload,
    extract_domain_data,
    validate_command_payload,
)

# Serialization
from .core.serialization import SerializationFormat

# Signals
from .core.signals import (
    PROCESS_CRASH_DETECTED,
    ActivityReport,
    CapabilitiesReport,
    CommandComplete,
    CommandError,
    Error,
    HealthResponse,
    ProcessCrashDetected,
    ProgressReport,
    Ready,
    ShutdownAck,
    StateReport,
    StreamCancelled,
    StreamChunk,
    StreamEnd,
    StreamError,
    StreamStarted,
)
from .core.simple_health_monitor import SimpleHealthMonitor

# State management
from .core.supervisor_state import SupervisorEvent, SupervisorState
from .core.types import (
    ProcessActivity,
    ProcessHealth,
    ProcessResourceUsage,
    ProcessState,
    ProcessStatus,
)

# Process management
from .process.supervisor import ProcessSupervisor
from .process.worker import WorkerProcess
from .utils.helpers import cleanup_socket_path, ensure_directory_exists

# Package metadata
__version__ = "4.0.0"
__author__ = "Process IPC Contributors"

# No automatic bootstrap - users must explicitly configure logging

# Public API exports - simplified stable set
__all__ = [
    # Core interfaces
    "Transport",  # Transport interface from universal_transport (imported from universal_transport.core.interfaces)
    "WorkerInterface",
    "EventBusProtocol",
    # Core types
    "ProcessStatus",
    "ProcessHealth",
    "ProcessState",
    "ProcessActivity",
    "ProcessResourceUsage",
    # Schema definitions
    "IPCMessage",
    "CommandPayload",
    "PingRequest",
    "PingResponse",
    "InferenceRequest",
    "InferenceResponse",
    "ErrorResponse",
    "DomainResponse",
    "extract_domain_data",
    "create_command_payload",
    "validate_command_payload",
    # Error types
    "ProcessIPCError",
    "ConnectionError",
    "ProcessError",
    "TransportError",
    "ProcessRecoveryError",
    "ProcessHealthError",
    # Process management
    "ProcessSupervisor",
    "WorkerProcess",
    # Transport layer (migrated to universal_transport)
    "AsyncUnixTransport",
    "AsyncUnixServer",
    "ProcessIPCCompatibleServer",
    "ProcessIPCCompatibleClient",
    "MessagePump",  # From universal_transport
    # Configuration
    "SupervisorConfig",
    "ProcessHealthConfig",
    "UnixSocketConfig",
    "ResourceMonitoringConfig",
    # State management
    "SupervisorState",
    "SupervisorEvent",
    # Serialization
    "SerializationFormat",
    # Signals
    "PROCESS_CRASH_DETECTED",
    # Signal factory functions
    "Ready",
    "ProcessCrashDetected",
    "StateReport",
    "ActivityReport",
    "ProgressReport",
    "CapabilitiesReport",
    "HealthResponse",
    "ShutdownAck",
    "CommandComplete",
    "CommandError",
    "StreamStarted",
    "StreamChunk",
    "StreamEnd",
    "StreamError",
    "StreamCancelled",
    "Error",
    # Utilities
    "get_logger",
    "cleanup_socket_path",
    "ensure_directory_exists",
    "SimpleHealthMonitor",
    # Validation helpers
    "validate_positive",
    "validate_non_negative",
    "validate_minimum",
    "validate_string_not_empty",
    "validate_choice",
]
