"""
Configuration system for process-ipc package.

Contains configuration classes and utilities for managing
process health monitoring, recovery, and other settings.
"""

from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config_validators import (
    validate_choice,
    validate_minimum,
    validate_non_negative,
    validate_positive,
    validate_string_not_empty,
)
from .interfaces import EventBusProtocol

# Transport Configuration Classes


@dataclass
class TransportConfig(ABC):
    """Base class for transport configuration with sensible defaults."""

    timeout: float = 30.0
    retry_attempts: int = 3


@dataclass
class UnixSocketConfig(TransportConfig):
    """
    Configuration for Unix socket transport.

    Only socket_path is required - everything else has sensible defaults.
    """

    socket_path: str = field(default="")  # REQUIRED - will be validated
    socket_permissions: int = 0o600  # Default: secure permissions
    backlog: int = 5  # Default: reasonable backlog
    max_message_size: int = (
        10 * 1024 * 1024
    )  # 10 MiB default (increased from 1MB to handle large tokenization requests)

    def __post_init__(self):
        validate_string_not_empty(self.socket_path, "socket_path")
        # Validate max_message_size
        if self.max_message_size <= 0:
            raise ValueError("max_message_size must be positive")
        if self.max_message_size > 10 * 1024 * 1024:  # 10 MiB practical limit
            raise ValueError("max_message_size cannot exceed 10 MiB")


# Health Configuration Classes


@dataclass
class ProcessHealthConfig:
    """
    Configuration for process health monitoring and recovery.

    Provides comprehensive settings for health monitoring, automatic recovery,
    and process verification with state-aware monitoring as the default.
    """

    # Health monitoring settings (always enabled)
    health_check_interval: float = 15.0  # seconds - faster for production
    health_check_timeout: float = 3.0  # seconds - faster for production

    # Auto-recovery settings
    auto_recovery: bool = True
    max_recovery_attempts: int = 3
    recovery_backoff: float = 5.0  # seconds
    recovery_timeout: float = 60.0  # seconds

    # Process verification settings
    verify_process_status: bool = True

    # Logging settings
    log_health_checks: bool = False
    log_recovery_attempts: bool = True

    # Advanced settings
    background_monitoring: bool = True
    start_monitoring_on_state: str = "READY"  # State to start monitoring on

    # Error output capture settings
    capture_error_output: bool = True
    max_error_output_size: int = 1024 * 1024  # 1MB
    preserve_error_output: bool = True  # Keep error output after process cleanup

    # NEW: Event-driven crash detection
    event_bus: EventBusProtocol | None = None
    """Event bus for publishing crash events (must implement EventBusProtocol)"""

    on_process_crash: Callable[[str, int, str], None] | None = None
    """Callback invoked when process crashes: (process_id, exit_code, error_msg)"""

    on_process_exit: Callable[[str, int], None] | None = None
    """Callback invoked when process exits: (process_id, exit_code)"""

    # Crash detection settings
    detect_crashes: bool = True
    """Enable automatic crash detection"""

    crash_exit_codes: list[int] | None = None
    """Explicit list of exit codes considered crashes (None = any non-zero)"""

    expected_exit_codes: list[int] = field(default_factory=lambda: [0])
    """Exit codes considered normal/expected (default: [0])"""

    publish_crash_events: bool = True
    """Publish crash events to event bus if available"""

    capture_stderr_on_crash: bool = True
    """Capture and include stderr in crash events"""

    crash_callback_timeout: float = 5.0
    """Maximum time to wait for crash callbacks (seconds)"""

    def is_crash_exit_code(self, exit_code: int) -> bool:
        """
        Determine if an exit code represents a crash.

        Args:
            exit_code: Process exit code

        Returns:
            bool: True if exit code indicates crash
        """
        # Signal-based terminations (negative exit codes) are always crashes
        if exit_code < 0:
            return True

        # Expected exit codes are not crashes
        if exit_code in self.expected_exit_codes:
            return False

        # If explicit crash codes defined, check against them
        if self.crash_exit_codes is not None:
            return exit_code in self.crash_exit_codes

        # Otherwise, any non-expected code is a crash
        return exit_code not in self.expected_exit_codes

    def __post_init__(self):
        """Validate configuration values after initialization."""
        validate_positive(self.health_check_interval, "health_check_interval")
        validate_positive(self.health_check_timeout, "health_check_timeout")
        validate_non_negative(self.max_recovery_attempts, "max_recovery_attempts")
        validate_non_negative(self.recovery_backoff, "recovery_backoff")
        validate_positive(self.recovery_timeout, "recovery_timeout")
        validate_positive(self.max_error_output_size, "max_error_output_size")
        validate_positive(self.crash_callback_timeout, "crash_callback_timeout")

        # Validate start_monitoring_on_state
        valid_states = [
            "STARTING",
            "INITIALIZING",
            "READY",
            "BUSY",
            "IDLE",
            "ERROR",
            "STOPPING",
            "STOPPED",
            "DEAD",
        ]
        validate_choice(
            self.start_monitoring_on_state, valid_states, "start_monitoring_on_state"
        )


@dataclass
class ResourceMonitoringConfig:
    """
    Configuration for process resource monitoring.

    Provides settings for RAM/VRAM monitoring with background collection.
    """

    # Resource monitoring settings
    enable_resource_monitoring: bool = True  # Enable by default for production
    monitoring_interval: float = 5.0  # seconds between resource checks
    history_size: int = 100  # number of resource snapshots to keep per process

    # GPU monitoring settings
    enable_gpu_monitoring: bool = True  # Try to monitor GPU if available
    gpu_collection_timeout: float = 2.0  # seconds

    # Thread pool settings
    max_workers: int = 4  # Thread pool size for non-blocking resource collection

    # Callbacks
    on_resource_update: Callable | None = (
        None  # Called with ProcessResourceUsage when updated
    )

    def __post_init__(self):
        """Validate configuration values after initialization."""
        validate_positive(self.monitoring_interval, "monitoring_interval")
        validate_minimum(self.history_size, 1, "history_size")
        validate_minimum(self.max_workers, 1, "max_workers")
        validate_positive(self.gpu_collection_timeout, "gpu_collection_timeout")


@dataclass
class ProcessRecoveryInfo:
    """
    Information about process recovery attempts.

    Tracks recovery history and state for a specific process.
    """

    process_id: str
    original_command: list
    original_socket_path: str
    original_env: dict[str, str] | None = None
    original_cwd: str | None = None
    recovery_attempts: int = 0
    last_recovery_attempt: datetime | None = None
    last_health_check: datetime | None = None
    is_recovering: bool = False

    def can_attempt_recovery(self, max_attempts: int) -> bool:
        """Check if recovery can be attempted."""
        return self.recovery_attempts < max_attempts and not self.is_recovering

    def record_recovery_attempt(self) -> None:
        """Record a recovery attempt."""
        self.recovery_attempts += 1
        self.last_recovery_attempt = datetime.now()
        self.is_recovering = True

    def mark_recovery_complete(self) -> None:
        """Mark recovery as complete."""
        self.is_recovering = False

    def reset_recovery_count(self) -> None:
        """Reset recovery attempt count (for successful recovery)."""
        self.recovery_attempts = 0
        self.is_recovering = False


@dataclass
class HealthCheckResult:
    """
    Result of a health check operation.

    Contains detailed information about the health check outcome.
    """

    process_id: str
    is_healthy: bool
    timestamp: datetime = field(default_factory=datetime.now)
    pid: int | None = None
    exit_code: int | None = None
    error_message: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "process_id": self.process_id,
            "is_healthy": self.is_healthy,
            "timestamp": self.timestamp.isoformat(),
            "pid": self.pid,
            "exit_code": self.exit_code,
            "error_message": self.error_message,
            "details": self.details,
        }


@dataclass
class SupervisorConfig:
    """
    Consolidated configuration for ProcessSupervisor.

    This class consolidates all configuration needed for process supervision,
    including health monitoring, resource monitoring, and transport settings.
    """

    # Transport configuration
    transport: UnixSocketConfig = field(
        default_factory=lambda: UnixSocketConfig(socket_path="/tmp/process_ipc.sock")
    )

    # Health monitoring configuration
    health: ProcessHealthConfig = field(default_factory=ProcessHealthConfig)

    # Resource monitoring configuration
    resource: ResourceMonitoringConfig = field(default_factory=ResourceMonitoringConfig)

    # Process management settings
    worker_startup_timeout: float = 30.0
    worker_shutdown_timeout: float = 10.0

    # Message pump settings
    message_pump_receive_timeout: float | None = 30.0
    """Timeout for MessagePump transport.receive() calls in seconds.
    Prevents indefinite hangs from incomplete socket data.
    Set to None to disable timeout (not recommended).
    Default: 30.0 seconds."""

    # Logging configuration
    log_level: str = "INFO"
    log_file: str | None = None

    @classmethod
    def from_socket_path(cls, socket_path: str) -> "SupervisorConfig":
        """
        Convenience constructor for simple cases.

        Args:
            socket_path: Unix socket path

        Returns:
            SupervisorConfig: Configured with socket path
        """
        return cls(transport=UnixSocketConfig(socket_path=socket_path))

    def __post_init__(self):
        """Validate configuration after initialization."""
        validate_positive(self.worker_startup_timeout, "worker_startup_timeout")
        validate_positive(self.worker_shutdown_timeout, "worker_shutdown_timeout")

        # Validate message_pump_receive_timeout if set
        if self.message_pump_receive_timeout is not None:
            validate_positive(
                self.message_pump_receive_timeout, "message_pump_receive_timeout"
            )


# ConfigManager removed - unused legacy code
