"""
Abstract base classes for process-ipc package.

Defines the core interfaces for process management implementations.
Transport interfaces are now provided by universal_transport.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, Protocol

from .types import (
    ProcessDiagnosticInfo,
    ProcessErrorOutput,
    ProcessHealth,
    ProcessStatus,
)

# Fallback for typing when universal_event_bus is not available
Event = dict[str, Any]


class ProcessManager(ABC):
    """
    Abstract base class for process lifecycle management.

    Provides the interface for spawning, monitoring, and communicating
    with worker processes. Handles process startup, health monitoring,
    and message routing.
    """

    @abstractmethod
    async def start_process(
        self,
        process_id: str,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        startup_timeout: float = 300.0,
    ) -> bool:
        """
        Start a new worker process.

        Args:
            process_id: Unique identifier for the process
            command: Command line arguments to start the process
            env: Environment variables for the process
            cwd: Working directory for the process
            startup_timeout: Maximum time to wait for process startup

        Returns:
            bool: True if process started successfully, False otherwise

        Raises:
            ProcessError: If process startup fails
            TimeoutError: If startup times out
        """
        pass

    @abstractmethod
    async def stop_process(
        self,
        process_id: str,
        force: bool = False,
        timeout: float = 30.0,
        fast_termination: bool = False,
    ) -> bool:
        """
        Stop a running worker process.

        Args:
            process_id: Unique identifier for the process
            force: If True, force kill the process
            timeout: Maximum time to wait for graceful shutdown
            fast_termination: If True, use immediate SIGKILL termination

        Returns:
            bool: True if process stopped successfully, False otherwise

        Raises:
            ProcessError: If process stop fails
        """
        pass

    @abstractmethod
    async def terminate_process_fast(self, process_id: str) -> bool:
        """
        Fast terminate a process using immediate SIGKILL.

        Bypasses graceful shutdown and immediately sends SIGKILL to the process.
        This is useful for stateless processes that can be terminated immediately
        without data loss concerns.

        Args:
            process_id: Unique identifier for the process

        Returns:
            bool: True if process terminated successfully, False otherwise

        Raises:
            ProcessError: If process termination fails
        """
        pass

    @abstractmethod
    async def get_process_status(self, process_id: str) -> ProcessStatus:
        """
        Get the current status of a process.

        Args:
            process_id: Unique identifier for the process

        Returns:
            ProcessStatus: Current process status

        Raises:
            ProcessError: If process not found
        """
        pass

    @abstractmethod
    async def health_check(
        self, process_id: str, timeout: float = 10.0
    ) -> ProcessHealth:
        """
        Perform health check on a process.

        Args:
            process_id: Unique identifier for the process
            timeout: Health check timeout in seconds

        Returns:
            ProcessHealth: Current process health status

        Raises:
            ProcessError: If health check fails
            TimeoutError: If health check times out
        """
        pass

    @abstractmethod
    async def send_command(self, process_id: str, command: dict[str, Any]) -> str:
        """
        Send command and return correlation ID for tracking.

        Args:
            process_id: Unique identifier for the process
            command: Command data to send

        Returns:
            str: Correlation ID for tracking completion

        Raises:
            ProcessError: If process not found or not ready
        """
        pass

    @abstractmethod
    async def wait_for_event(
        self, correlation_id: str, timeout: float | None = None
    ) -> dict[str, Any]:
        """
        Wait for event by correlation ID.

        Args:
            correlation_id: Correlation ID to wait for
            timeout: Optional timeout in seconds (None = wait indefinitely)

        Returns:
            Dict[str, Any]: Event data when received

        Raises:
            asyncio.TimeoutError: If timeout is exceeded
        """
        pass

    @abstractmethod
    def on_event(self, event_type: str, handler, process_id: str | None = None):
        """
        Subscribe to events from workers.

        Args:
            event_type: Type of event to subscribe to
            handler: Async function to handle events
            process_id: Optional process ID to filter events from

        Returns:
            Subscription object for management
        """
        pass

    @abstractmethod
    async def list_processes(self) -> list[str]:
        """
        List all managed process IDs.

        Returns:
            List[str]: List of process IDs
        """
        pass

    @abstractmethod
    async def shutdown(self) -> bool:
        """
        Shutdown the process manager and cleanup all resources.

        Stops all managed processes and cleans up connections.
        Should be idempotent and safe to call multiple times.

        Returns:
            bool: True if all processes shut down successfully, False if any failed
        """
        pass

    @abstractmethod
    async def get_process_error_output(
        self, process_id: str
    ) -> Optional["ProcessErrorOutput"]:
        """
        Get error output from a crashed or terminated process.

        Args:
            process_id: ID of the process to get error output for

        Returns:
            ProcessErrorOutput if available, None if no error output captured
        """
        pass

    @abstractmethod
    async def get_process_diagnostic_info(
        self, process_id: str
    ) -> "ProcessDiagnosticInfo":
        """
        Get comprehensive diagnostic information for a process.

        Args:
            process_id: ID of the process to get diagnostics for

        Returns:
            ProcessDiagnosticInfo containing all available diagnostic information
        """
        pass

    @abstractmethod
    def get_process_exit_code(self, process_id: str) -> int | None:
        """
        Get the exit code of a terminated process.

        Args:
            process_id: ID of the process

        Returns:
            Exit code if process has terminated, None if still running
        """
        pass


class WorkerInterface(ABC):
    """
    Abstract base class for worker process implementations.

    Defines the interface that worker processes must implement
    to participate in the IPC system.
    """

    @abstractmethod
    async def initialize(self, socket_path: str) -> None:
        """
        Initialize the worker process and establish IPC connection.

        Args:
            socket_path: Unix socket path for IPC communication

        Raises:
            ConnectionError: If IPC connection fails
        """
        pass

    @abstractmethod
    async def process_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """
        Process command asynchronously - return result for event emission.

        Commands are processed in the background and results are emitted as events.

        Args:
            command: Command data to process

        Returns:
            Dict[str, Any]: Command result data

        Raises:
            Exception: Any processing errors (will be emitted as error events)
        """
        pass

    @abstractmethod
    async def emit_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        """
        Emit event to manager.

        Args:
            event_type: Type of event being emitted
            event_data: Event data payload
        """
        pass

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """
        Perform internal health check.

        Returns:
            Dict[str, Any]: Health status information
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """
        Shutdown the worker process gracefully.

        Should cleanup resources and close IPC connections.
        """
        pass


class EventBusProtocol(Protocol):
    """
    Protocol for event bus integration.

    Any event bus implementation must provide a publish method
    that accepts Event instances for crash detection and other events.
    """

    def publish(self, event: Event) -> None:
        """
        Publish an Event instance to the event bus.

        Args:
            event: Event instance with signal, payload, timestamp, etc.
                  Should be a universal_event_bus.Event instance or
                  compatible object with the required attributes.
        """
        ...
