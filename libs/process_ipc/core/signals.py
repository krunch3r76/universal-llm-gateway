"""
Signal constants for the Universal Event Bus compatible message structure.

Defines all signals (message types) used in the IPC system, matching the
Event Bus schema where messages have: signal, payload, id, timestamp, correlation_id

Factory functions enforce payload structure consistency across process boundaries.
"""
# ruff: noqa: N802  # Factory functions use PascalCase to match event constants

from typing import Any

# Process lifecycle signals
READY = "ready"
SHUTDOWN = "shutdown"
SHUTDOWN_ACK = "shutdown_ack"

# Health check signals
HEALTH_CHECK = "health_check"
HEALTH_RESPONSE = "health_response"

# Command signals
COMMAND = "command"
COMMAND_STARTED = "command_started"
COMMAND_COMPLETE = "command_complete"
COMMAND_ERROR = "command_error"

# Event signals
EVENT = "event"

# State reporting signals
STATE_REPORT = "state_report"
ACTIVITY_REPORT = "activity_report"
PROGRESS_REPORT = "progress_report"
CAPABILITIES_REPORT = "capabilities_report"

# Streaming signals
STREAM_START = "stream_start"
STREAM_STARTED = "stream_started"
STREAM_CHUNK = "stream_chunk"
STREAM_END = "stream_end"
STREAM_ERROR = "stream_error"
CANCEL_STREAM = "cancel_stream"
STREAM_CANCELLED = "stream_cancelled"

# Data transfer signals (efficient single-message transfer)
DATA_STREAM = "data_stream"  # Send entire payload in one message

# Error signals
ERROR = "error"

# Process crash signals
PROCESS_CRASH_DETECTED = "PROCESS_CRASH_DETECTED"

# All valid signals for validation
ALL_SIGNALS = {
    READY,
    SHUTDOWN,
    SHUTDOWN_ACK,
    HEALTH_CHECK,
    HEALTH_RESPONSE,
    COMMAND,
    COMMAND_STARTED,
    COMMAND_COMPLETE,
    COMMAND_ERROR,
    EVENT,
    STATE_REPORT,
    ACTIVITY_REPORT,
    PROGRESS_REPORT,
    CAPABILITIES_REPORT,
    STREAM_START,
    STREAM_STARTED,
    STREAM_CHUNK,
    STREAM_END,
    STREAM_ERROR,
    CANCEL_STREAM,
    STREAM_CANCELLED,
    DATA_STREAM,
    ERROR,
    PROCESS_CRASH_DETECTED,
}


# ============================================================================
# Factory Functions
# ============================================================================
# IPC signals cross process boundaries. Factory functions enforce payload
# structure consistency and provide type hints at call sites.
#
# Note: These return dict[str, Any] for IPC transport compatibility, not Event.
# IPC uses correlation_id at top level for request/response tracking.


def Ready(
    worker_id: str,
    status: str,
    worker_status: dict[str, Any],
    correlation_id: str | None = None,
    **ready_info: Any,
) -> dict[str, Any]:
    """
    Create READY signal when worker is initialized.

    Args:
        worker_id: Unique worker identifier
        status: Worker status (e.g., "loaded")
        worker_status: Dict with worker state details
        correlation_id: Optional correlation ID for request/response tracking
        **ready_info: Additional initialization info

    Returns:
        IPC message dict with READY signal
    """
    from .messages import create_message

    return create_message(
        signal=READY,
        payload={
            "worker_id": worker_id,
            "status": status,
            "worker_status": worker_status,
            **ready_info,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def ProcessCrashDetected(
    process_id: str,
    error_message: str,
    exit_code: int,
    pid: int,
    socket_path: str | None,
    stderr: str | None,
    is_signal_termination: bool,
    signal_name: str | None,
) -> dict[str, Any]:
    """
    Create PROCESS_CRASH_DETECTED signal.

    Args:
        process_id: Crashed process identifier
        error_message: Human-readable error description
        exit_code: Process exit code (negative for signals)
        pid: Process ID
        socket_path: Unix socket path (if applicable)
        stderr: Captured stderr output
        is_signal_termination: True if terminated by signal
        signal_name: Signal name if terminated by signal

    Returns:
        IPC message dict with PROCESS_CRASH_DETECTED signal

    Note:
        This signal is emitted directly to event_bus, not via IPC transport.
        However, we maintain the factory pattern for consistency.
    """
    from universal_event_bus import Event, event_factory

    return Event(
        signal=PROCESS_CRASH_DETECTED,
        payload={
            "process_id": process_id,
            "error_message": error_message,
            "exit_code": exit_code,
            "pid": pid,
            "socket_path": socket_path,
            "stderr": stderr,
            "is_signal_termination": is_signal_termination,
            "signal_name": signal_name,
        }
    )


def StateReport(
    worker_id: str,
    state: str,
    details: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create STATE_REPORT signal.

    Args:
        worker_id: Unique worker identifier
        state: Current state value
        details: Additional state details
        correlation_id: Optional correlation ID for request/response tracking

    Returns:
        IPC message dict with STATE_REPORT signal
    """
    from .messages import create_message

    return create_message(
        signal=STATE_REPORT,
        payload={
            "worker_id": worker_id,
            "state": state,
            "details": details,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def ActivityReport(
    worker_id: str,
    activity_type: str,
    details: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create ACTIVITY_REPORT signal.

    Args:
        worker_id: Unique worker identifier
        activity_type: Type of activity (e.g., 'loading_model', 'processing_inference')
        details: Additional activity details
        correlation_id: Optional correlation ID for request/response tracking

    Returns:
        IPC message dict with ACTIVITY_REPORT signal
    """
    from .messages import create_message

    return create_message(
        signal=ACTIVITY_REPORT,
        payload={
            "worker_id": worker_id,
            "activity_type": activity_type,
            "details": details,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def ProgressReport(
    worker_id: str,
    progress: float,
    message: str | None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create PROGRESS_REPORT signal.

    Args:
        worker_id: Unique worker identifier
        progress: Progress value (0.0 to 1.0)
        message: Optional progress message
        correlation_id: Optional correlation ID for request/response tracking

    Returns:
        IPC message dict with PROGRESS_REPORT signal
    """
    from .messages import create_message

    return create_message(
        signal=PROGRESS_REPORT,
        payload={
            "worker_id": worker_id,
            "progress": progress,
            "message": message,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def CapabilitiesReport(
    worker_id: str,
    capabilities: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create CAPABILITIES_REPORT signal.

    Args:
        worker_id: Unique worker identifier
        capabilities: Worker capabilities (loaded models, etc.)
        correlation_id: Optional correlation ID for request/response tracking

    Returns:
        IPC message dict with CAPABILITIES_REPORT signal
    """
    from .messages import create_message

    return create_message(
        signal=CAPABILITIES_REPORT,
        payload={
            "worker_id": worker_id,
            "capabilities": capabilities,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def HealthResponse(
    worker_id: str,
    status: str,
    healthy: bool,
    details: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create HEALTH_RESPONSE signal.

    Args:
        worker_id: Unique worker identifier
        status: Health status string (e.g., "healthy", "unhealthy")
        healthy: Boolean health indicator
        details: Additional health details
        correlation_id: Optional correlation ID for request/response tracking

    Returns:
        IPC message dict with HEALTH_RESPONSE signal
    """
    from .messages import create_message

    return create_message(
        signal=HEALTH_RESPONSE,
        payload={
            "worker_id": worker_id,
            "status": status,
            "healthy": healthy,
            "details": details,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def ShutdownAck(
    worker_id: str,
    status: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create SHUTDOWN_ACK signal.

    Args:
        worker_id: Unique worker identifier
        status: Shutdown acknowledgment status (e.g., "acknowledged")
        correlation_id: Optional correlation ID for request/response tracking

    Returns:
        IPC message dict with SHUTDOWN_ACK signal
    """
    from .messages import create_message

    return create_message(
        signal=SHUTDOWN_ACK,
        payload={
            "worker_id": worker_id,
            "status": status,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def CommandError(
    worker_id: str,
    error: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create COMMAND_ERROR signal.

    Args:
        worker_id: Unique worker identifier
        error: Error message
        correlation_id: Optional correlation ID for request/response tracking

    Returns:
        IPC message dict with COMMAND_ERROR signal
    """
    from .messages import create_message

    return create_message(
        signal=COMMAND_ERROR,
        payload={
            "worker_id": worker_id,
            "error": error,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def StreamStarted(
    status: str,
    correlation_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """
    Create STREAM_STARTED signal.

    Args:
        status: Stream start status (e.g., "started")
        correlation_id: Optional correlation ID for request/response tracking
        worker_id: Optional worker ID for message ID generation

    Returns:
        IPC message dict with STREAM_STARTED signal
    """
    from .messages import create_message

    return create_message(
        signal=STREAM_STARTED,
        payload={
            "status": status,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def StreamChunk(
    chunk_id: int,
    data: Any,
    total_chunks: int,
    correlation_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """
    Create STREAM_CHUNK signal.

    Args:
        chunk_id: Sequential chunk identifier
        data: Chunk data payload
        total_chunks: Total number of chunks
        correlation_id: Optional correlation ID for request/response tracking
        worker_id: Optional worker ID for message ID generation

    Returns:
        IPC message dict with STREAM_CHUNK signal
    """
    from .messages import create_message

    return create_message(
        signal=STREAM_CHUNK,
        payload={
            "chunk_id": chunk_id,
            "data": data,
            "total_chunks": total_chunks,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def StreamEnd(
    total_chunks: int,
    status: str,
    correlation_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """
    Create STREAM_END signal.

    Args:
        total_chunks: Total number of chunks sent
        status: Stream completion status (e.g., "completed")
        correlation_id: Optional correlation ID for request/response tracking
        worker_id: Optional worker ID for message ID generation

    Returns:
        IPC message dict with STREAM_END signal
    """
    from .messages import create_message

    return create_message(
        signal=STREAM_END,
        payload={
            "total_chunks": total_chunks,
            "status": status,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def StreamError(
    error: str,
    chunk_count: int,
    correlation_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """
    Create STREAM_ERROR signal.

    Args:
        error: Error message
        chunk_count: Number of chunks sent before error
        correlation_id: Optional correlation ID for request/response tracking
        worker_id: Optional worker ID for message ID generation

    Returns:
        IPC message dict with STREAM_ERROR signal
    """
    from .messages import create_message

    return create_message(
        signal=STREAM_ERROR,
        payload={
            "error": error,
            "chunk_count": chunk_count,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def StreamCancelled(
    status: str,
    correlation_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """
    Create STREAM_CANCELLED signal.

    Args:
        status: Cancellation status (e.g., "cancelled")
        correlation_id: Optional correlation ID for request/response tracking
        worker_id: Optional worker ID for message ID generation

    Returns:
        IPC message dict with STREAM_CANCELLED signal
    """
    from .messages import create_message

    return create_message(
        signal=STREAM_CANCELLED,
        payload={
            "status": status,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def CommandComplete(
    result: dict[str, Any],
    correlation_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """
    Create COMMAND_COMPLETE signal for successful command execution.

    Args:
        result: Command execution result (worker response)
        correlation_id: Request correlation ID
        worker_id: Worker identifier for message ID generation

    Returns:
        IPC message dict with COMMAND_COMPLETE signal
    """
    from .messages import create_message

    return create_message(
        signal=COMMAND_COMPLETE,
        payload=result,
        correlation_id=correlation_id,
        worker_id=worker_id,
    )


def Error(
    worker_id: str,
    error: str,
    original_signal: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create ERROR signal.

    Args:
        worker_id: Unique worker identifier
        error: Error message
        original_signal: Original signal that caused the error
        correlation_id: Optional correlation ID for request/response tracking

    Returns:
        IPC message dict with ERROR signal
    """
    from .messages import create_message

    return create_message(
        signal=ERROR,
        payload={
            "worker_id": worker_id,
            "error": error,
            "original_signal": original_signal,
        },
        correlation_id=correlation_id,
        worker_id=worker_id,
    )
