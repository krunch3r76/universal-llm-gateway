"""Map step progress events."""

from universal_event_bus import Event, event_factory


@event_factory
def MapStepStarted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    total_iterations: int,
    timeout_seconds: float | None,
    threshold: int | float | None,
) -> Event:
    """
    Emitted when map step begins execution.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Map step name
        total_iterations: Number of iterations to execute
        timeout_seconds: Configured timeout (None if no timeout)
        threshold: Success threshold (count or percentage)
    """
    return Event(
        signal="pipeline.map.started",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "total_iterations": total_iterations,
            "timeout_seconds": timeout_seconds,
            "threshold": threshold,
        },
    )


@event_factory
def MapIterationStarted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    iteration_index: int,
    model_id: str | None,
    gateway_id: str | None,
) -> Event:
    """
    Emitted when single map iteration is dispatched.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Map step name
        iteration_index: Zero-based iteration index
        model_id: Target model (if applicable)
        gateway_id: Target gateway (if known)
    """
    return Event(
        signal="pipeline.map.iteration.started",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "iteration_index": iteration_index,
            "model_id": model_id,
            "gateway_id": gateway_id,
        },
    )


@event_factory
def MapIterationCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    iteration_index: int,
    duration_seconds: float,
) -> Event:
    """Emitted when single map iteration completes successfully."""
    return Event(
        signal="pipeline.map.iteration.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "iteration_index": iteration_index,
            "duration_seconds": duration_seconds,
        },
    )


@event_factory
def MapIterationFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    iteration_index: int,
    error: str,
    duration_seconds: float | None,
    failure_type: str,  # "error" | "timeout" | "cancelled"
) -> Event:
    """
    Emitted when single map iteration fails.

    Payload includes failure_type to distinguish timeout from error.
    """
    return Event(
        signal="pipeline.map.iteration.failed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "iteration_index": iteration_index,
            "error": error,
            "duration_seconds": duration_seconds,
            "failure_type": failure_type,
        },
    )


@event_factory
def MapTimeoutWarning(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    elapsed_seconds: float,
    timeout_seconds: float,
    pending_iterations: list[int],
    completed_iterations: int,
) -> Event:
    """
    Emitted when map step approaches timeout (75% and 90% thresholds).

    Allows proactive alerting before failure.
    """
    return Event(
        signal="pipeline.map.timeout.warning",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "elapsed_seconds": elapsed_seconds,
            "timeout_seconds": timeout_seconds,
            "pending_iterations": pending_iterations,
            "completed_iterations": completed_iterations,
            "percent_elapsed": round(elapsed_seconds / timeout_seconds * 100, 1),
        },
    )


@event_factory
def MapStepCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    succeeded_count: int,
    failed_count: int,
    total_count: int,
    duration_seconds: float,
    met_threshold: bool,
) -> Event:
    """Emitted when map step finishes (success or failure)."""
    return Event(
        signal="pipeline.map.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
            "total_count": total_count,
            "duration_seconds": duration_seconds,
            "met_threshold": met_threshold,
        },
    )
