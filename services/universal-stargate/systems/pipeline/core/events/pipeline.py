"""Pipeline lifecycle events."""

from universal_event_bus import Event, event_factory


@event_factory
def PipelineStarted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    domain: str,
    step_count: int,
    timeout_seconds: float | None,
) -> Event:
    """
    Emitted when pipeline execution begins.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        domain: Pipeline domain/type
        step_count: Total number of steps in pipeline
        timeout_seconds: Overall pipeline timeout (None if no timeout)
    """
    return Event(
        signal="pipeline.started",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "domain": domain,
            "step_count": step_count,
            "timeout_seconds": timeout_seconds,
        },
    )


@event_factory
def PipelineCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    duration_seconds: float,
    step_count: int,
    output_step: str,
) -> Event:
    """
    Emitted when pipeline completes successfully.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        duration_seconds: Total execution time
        step_count: Number of steps executed
        output_step: Final output step name
    """
    return Event(
        signal="pipeline.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "duration_seconds": duration_seconds,
            "step_count": step_count,
            "output_step": output_step,
        },
    )


@event_factory
def PipelineFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    duration_seconds: float,
    error: str,
    failed_step: str | None,
) -> Event:
    """
    Emitted when pipeline execution fails.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        duration_seconds: Time until failure
        error: Error message
        failed_step: Step that failed (None if failure before step execution)
    """
    return Event(
        signal="pipeline.failed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "duration_seconds": duration_seconds,
            "error": error,
            "failed_step": failed_step,
        },
    )


@event_factory
def PipelineCancelled(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    duration_seconds: float,
    reason: str,
    completed_steps: int,
    pending_steps: int,
) -> Event:
    """
    Emitted when pipeline execution is cancelled (e.g., client disconnect).

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        duration_seconds: Time until cancellation
        reason: Cancellation reason (e.g., "client_disconnected")
        completed_steps: Number of steps completed before cancellation
        pending_steps: Number of steps that were pending/running
    """
    return Event(
        signal="pipeline.cancelled",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "duration_seconds": duration_seconds,
            "reason": reason,
            "completed_steps": completed_steps,
            "pending_steps": pending_steps,
        },
    )
