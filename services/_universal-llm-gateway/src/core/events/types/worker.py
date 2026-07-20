"""Worker process health and crash event signals and factories.

Signals worker load coordination, crash detection, orphaned sockets, and
health-check failures. Consumed by process_crash_bridge, socket cleanup,
and crash event handlers.
"""

# ruff: noqa: N802 - Factory function names match event signal names

from universal_event_bus import Event, event_factory

# ========== Worker Crash Detection Event Signals ==========

WORKER_LOADING = "worker.loading"
"""
Emitted when the gateway begins loading a model into a worker slot.

Carries estimated VRAM so downstream services can anticipate cold-load
duration and avoid stampeding the worker with concurrent requests.

Payload:
    model_id: str - Model being loaded
    estimated_vram_mb: int - Estimated VRAM requirement from catalog
    trigger: str - What triggered the load ("on_demand" | "explicit")
"""

WORKER_CRASH_DETECTED = "worker.crash.detected"
"""
Emitted when a worker process crashes unexpectedly.

Payload:
    model_id: str - Model ID of the crashed worker
    error_message: str - Error message describing the crash
    socket_path: str - Path to the orphaned socket file
    process_pid: Optional[int] - PID of the crashed process
"""

SOCKET_ORPHANED = "socket.orphaned"
"""
Emitted when an orphaned socket file is detected and cleaned up.

Payload:
    model_id: str - Model ID associated with the orphaned socket
    socket_path: str - Path to the orphaned socket file
    cleanup_successful: bool - Whether cleanup was successful
    error: Optional[str] - Error message if cleanup failed
"""

HEALTH_CHECK_FAILED = "health.check.failed"
"""
Emitted when a health check fails for a worker process.

Payload:
    model_id: str - Model ID of the worker
    error_message: str - Error message describing the failure
    socket_path: str - Path to the socket file
"""


# Worker Lifecycle Event Factories
@event_factory
def WorkerLoading(
    model_id: str,
    estimated_vram_mb: int,
    trigger: str = "on_demand",
) -> Event:
    """Create WORKER_LOADING event.

    Coordination signal: downstream services should anticipate a cold-load
    window before the model can serve inference.

    Args:
        model_id: Model being loaded.
        estimated_vram_mb: Estimated VRAM requirement from catalog.
        trigger: What triggered the load ("on_demand" or "explicit").
    """
    return Event(
        signal=WORKER_LOADING,
        payload={
            "model_id": model_id,
            "estimated_vram_mb": estimated_vram_mb,
            "trigger": trigger,
        },
        role="coordination",
        scope="global",
    )


@event_factory
def WorkerCrashDetected(
    model_id: str,
    error_message: str,
    socket_path: str,
    process_pid: int | None = None,
    exit_code: int | None = None,
) -> Event:
    """
    Create WORKER_CRASH_DETECTED event.

    Args:
        model_id: Model ID of crashed worker
        error_message: Error message describing the crash
        socket_path: Path to the orphaned socket file
        process_pid: Optional PID of crashed process
        exit_code: Optional process exit code captured by process_ipc

    Returns:
        Event with WorkerCrashDetected signal
    """
    return Event(
        signal=WORKER_CRASH_DETECTED,
        payload={
            "model_id": model_id,
            "error_message": error_message,
            "socket_path": socket_path,
            "process_pid": process_pid,
            "exit_code": exit_code,
        },
    )


@event_factory
def SocketOrphaned(
    model_id: str,
    socket_path: str,
    cleanup_successful: bool,
    error: str | None = None,
) -> Event:
    """
    Create SOCKET_ORPHANED event.

    Args:
        model_id: Model ID associated with the orphaned socket
        socket_path: Path to the orphaned socket file
        cleanup_successful: Whether cleanup was successful
        error: Optional error message if cleanup failed

    Returns:
        Event with SocketOrphaned signal
    """
    return Event(
        signal=SOCKET_ORPHANED,
        payload={
            "model_id": model_id,
            "socket_path": socket_path,
            "cleanup_successful": cleanup_successful,
            "error": error,
        },
    )


@event_factory
def HealthCheckFailed(
    model_id: str,
    error_message: str,
    socket_path: str,
) -> Event:
    """
    Create HEALTH_CHECK_FAILED event.

    Args:
        model_id: Model ID of the worker
        error_message: Error message describing the failure
        socket_path: Path to the socket file

    Returns:
        Event with HealthCheckFailed signal
    """
    return Event(
        signal=HEALTH_CHECK_FAILED,
        payload={
            "model_id": model_id,
            "error_message": error_message,
            "socket_path": socket_path,
        },
    )
