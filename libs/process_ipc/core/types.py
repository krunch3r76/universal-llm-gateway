"""
Type definitions and enums for process-ipc package.

Contains all the type definitions, enums, and data structures
used throughout the IPC system.
"""

from datetime import datetime
from enum import Enum
from typing import Any, NamedTuple, Optional


class ProcessStatus(Enum):
    """
    Enumeration of possible process states.

    Represents the lifecycle states of a managed worker process.
    """

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


class ProcessHealth(Enum):
    """
    Enumeration of process health states.

    Represents the health status of a running process.
    """

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ProcessState(Enum):
    """Enhanced process states with lifecycle awareness."""

    STARTING = "starting"  # Process is starting up
    INITIALIZING = "initializing"  # Process is initializing (loading models, etc.)
    READY = "ready"  # Process is ready for work
    BUSY = "busy"  # Process is currently working
    IDLE = "idle"  # Process is ready but not working
    ERROR = "error"  # Process encountered an error
    STOPPING = "stopping"  # Process is shutting down
    STOPPED = "stopped"  # Process has stopped
    DEAD = "dead"  # Process died unexpectedly


class ProcessActivity(NamedTuple):
    """Information about current process activity."""

    activity_type: str  # e.g., 'loading_model', 'processing_inference'
    started_at: datetime
    progress: float | None = None  # 0.0 to 1.0
    details: dict[str, Any] | None = None
    estimated_completion: datetime | None = None


class ProcessErrorOutput(NamedTuple):
    """
    Error output information from a crashed or terminated process.

    Contains detailed information about process failures for debugging.
    """

    process_id: str
    exit_code: int | None
    stdout: str
    stderr: str
    combined_output: str
    crash_reason: str
    timestamp: datetime
    issues: list[str] = []  # List of identified issues/suggestions
    log_file_path: str | None = None


class ProcessDiagnosticInfo(NamedTuple):
    """
    Comprehensive diagnostic information for a process.

    Contains all available diagnostic information for troubleshooting.
    """

    process_id: str
    process_info: Optional["ProcessInfo"] = None
    error_output: ProcessErrorOutput | None = None
    health_status: ProcessHealth | None = None
    last_health_check: datetime | None = None
    recovery_attempts: int = 0
    last_recovery_attempt: datetime | None = None
    system_info: dict[str, Any] | None = None  # System resources, etc.
    log_file_exists: bool = False
    log_file_size: int | None = None


class EnhancedProcessInfo(NamedTuple):
    """Enhanced process information with state awareness."""

    process_id: str
    pid: int | None
    state: ProcessState
    health: ProcessHealth
    command: list
    socket_path: str
    started_at: datetime | None
    last_health_check: datetime | None
    env: dict[str, str] | None
    cwd: str | None
    # New state-aware fields
    current_activity: ProcessActivity | None = None
    capabilities: dict[str, Any] | None = None  # What the process can do
    last_state_change: datetime | None = None
    state_history: list[tuple[ProcessState, datetime]] = []


class ProcessInfo(NamedTuple):
    """
    Information about a managed process.

    Contains metadata and status information for a worker process.
    """

    process_id: str
    pid: int | None
    status: ProcessStatus
    health: ProcessHealth
    command: list
    socket_path: str
    started_at: datetime | None
    last_health_check: datetime | None
    env: dict[str, str] | None
    cwd: str | None
    # Recovery and health monitoring fields
    original_command: list | None = None
    original_socket_path: str | None = None
    original_env: dict[str, str] | None = None
    original_cwd: str | None = None
    recovery_attempts: int = 0
    last_recovery_attempt: datetime | None = None


# MessageType enum removed - use signals from core.signals instead


# Message NamedTuple removed - use message structure from core.messages instead


class HealthCheckResult(NamedTuple):
    """
    Result of a health check operation.

    Contains health status and optional diagnostic information.
    """

    health: ProcessHealth
    timestamp: datetime
    details: dict[str, Any] | None = None
    error: str | None = None


class ConnectionConfig(NamedTuple):
    """
    Configuration for IPC connections.

    Contains connection parameters and timeout settings.
    """

    address: str
    connect_timeout: float = 30.0
    send_timeout: float = 30.0
    receive_timeout: float = 30.0
    retry_attempts: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0


class ProcessConfig(NamedTuple):
    """
    Configuration for process management.

    Contains process startup and management parameters.
    """

    command: list
    env: dict[str, str] | None = None
    cwd: str | None = None
    startup_timeout: float = 300.0
    health_check_interval: float = 30.0
    health_check_timeout: float = 10.0
    shutdown_timeout: float = 30.0
    restart_on_failure: bool = False
    max_restarts: int = 3


class ProcessResourceUsage(NamedTuple):
    """
    Process resource usage snapshot.

    Contains current resource usage information for a process.
    """

    process_id: str
    pid: int
    timestamp: datetime
    # Memory usage in bytes
    ram_used: int
    ram_percent: float
    # GPU memory usage in bytes (None if no GPU or not available)
    vram_used: int | None = None
    vram_total: int | None = None
    vram_percent: float | None = None
    # System-wide context
    system_ram_total: int | None = None
    system_ram_available: int | None = None
    # Additional process info
    cpu_percent: float | None = None
    num_threads: int | None = None
