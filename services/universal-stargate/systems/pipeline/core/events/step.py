"""Step lifecycle events."""

from universal_event_bus import Event, event_factory


@event_factory
def StepStarted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    step_type: str,
    model_id: str | None,
    is_map_step: bool,
) -> Event:
    """
    Emitted when step execution begins (includes both regular and map steps).

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        step_type: Step type (e.g., "generate", "filter")
        model_id: Target model identifier (None if not applicable)
        is_map_step: True if step uses map execution mode
    """
    return Event(
        signal="pipeline.step.started",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "step_type": step_type,
            "model_id": model_id,
            "is_map_step": is_map_step,
        },
    )


@event_factory
def StepCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    duration_seconds: float,
    output_length: int,
    prompt_tokens: int,
    completion_tokens: int,
    model_call_count: int,
) -> Event:
    """Emitted when step completes successfully."""
    return Event(
        signal="pipeline.step.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "duration_seconds": duration_seconds,
            "output_length": output_length,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "model_call_count": model_call_count,
        },
    )


@event_factory
def StepFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    duration_seconds: float | None,
    error: str,
) -> Event:
    """
    Emitted when step execution fails.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        duration_seconds: Time until failure (None if failed before execution)
        error: Error message
    """
    return Event(
        signal="pipeline.step.failed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "duration_seconds": duration_seconds,
            "error": error,
        },
    )


@event_factory
def StepSkipped(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    reason: str,
) -> Event:
    """
    Emitted when step is skipped due to condition evaluation.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        reason: Why step was skipped
    """
    return Event(
        signal="pipeline.step.skipped",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "reason": reason,
        },
    )
