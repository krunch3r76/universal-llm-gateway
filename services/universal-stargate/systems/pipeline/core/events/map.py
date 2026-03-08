"""Map step progress events."""

from typing import Any

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
    Event factory for when a map step begins execution.

    Emitted when map step begins execution.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Map step name
        total_iterations: Number of iterations to execute
        timeout_seconds: `float | None` timeout budget for map wall-clock guard
        threshold: `int | float | None` success threshold:
            - int: minimum successful iterations required
            - float: ratio (0.0-1.0) of successes required
            - None: all iterations must succeed
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
    request_id: str | None = None,
) -> Event:
    """
    Event factory for when one map iteration is dispatched.

    Emitted when single map iteration is dispatched.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Map step name
        iteration_index: Zero-based iteration index
        model_id: Target model (if applicable)
        gateway_id: Target gateway (if known)
        request_id: Pre-generated request ID for event correlation (if set)
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
            "request_id": request_id,
        },
    )


@event_factory
def MapIterationInferenceStarted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    iteration_index: int,
    request_id: str,
    model_id: str | None,
    queue_wait_seconds: float,
) -> Event:
    """
    Event factory for when inference begins for a map iteration.

    Emitted when inference actually begins for a map iteration.

    Bridges Stargate request runtime-start telemetry into pipeline observability.
    queue_wait_seconds = inference_started_at - submitted_at.
    """
    return Event(
        signal="pipeline.map.iteration.inference.started",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "iteration_index": iteration_index,
            "request_id": request_id,
            "model_id": model_id,
            "queue_wait_seconds": queue_wait_seconds,
        },
    )


@event_factory
def MapIterationInferenceFallback(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    iteration_index: int,
    request_id: str,
    fallback_signal: str,
    reason: str,
) -> Event:
    """
    Event factory for when fallback inference boundary timing is used.

    Emitted at iteration completion when fallback boundary timing was used.

    This only fires when the primary `request.inference.started` signal never
    arrived before iteration completion.
    """
    return Event(
        signal="pipeline.map.iteration.inference.fallback",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "iteration_index": iteration_index,
            "request_id": request_id,
            "fallback_signal": fallback_signal,
            "reason": reason,
        },
    )


@event_factory
def MapIterationInferenceLost(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    iteration_index: int,
    request_id: str,
) -> Event:
    """
    Event factory for when no inference boundary signal was observed.

    Emitted when neither primary nor fallback inference boundary arrived.

    Indicates a complete observability gap for this iteration queue-wait
    boundary.
    """
    return Event(
        signal="pipeline.map.iteration.inference.lost",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "iteration_index": iteration_index,
            "request_id": request_id,
        },
    )


@event_factory
def MapIterationCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    iteration_index: int,
    elapsed_seconds: float,
    inference_seconds: float | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> Event:
    """
    Event factory for when one map iteration completes successfully.

    Emitted when one map iteration completes successfully.

    Note: this is the global event-bus signal used by pipeline observers.
    Recorder-side lifecycle events use a separate model with richer payload.
    """
    return Event(
        signal="pipeline.map.iteration.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "iteration_index": iteration_index,
            "elapsed_seconds": elapsed_seconds,
            "inference_seconds": inference_seconds,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
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
    failure_type: str,
    truncated_response: str | None = None,
    truncation_tokens: int | None = None,
) -> Event:
    """
    Event factory for when one map iteration fails.

    Emitted when single map iteration fails.

    Payload includes failure_type with values:
    - "error": handler failure
    - "timeout": outer map timeout
    - "inference_timeout": per-iteration timeout after inference started
    - "cancelled": cancellation by fail-fast or external cancellation

    When failure_type is "error" and the cause is response truncation,
    truncated_response contains the partial model output and truncation_tokens
    the completion token count at the point of truncation.
    """
    payload: dict[str, Any] = {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "step_name": step_name,
        "iteration_index": iteration_index,
        "error": error,
        "duration_seconds": duration_seconds,
        "failure_type": failure_type,
    }
    if truncated_response is not None:
        payload["truncated_response_path"] = truncated_response
        payload["truncation_tokens"] = truncation_tokens
    return Event(signal="pipeline.map.iteration.failed", payload=payload)


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
    Event factory for when a map step approaches timeout.

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
    """
    Event factory for when a map step finishes (success or failure).

    Emitted when all map iterations have reached a terminal state and the step
    can report aggregate outcomes.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Map step name
        succeeded_count: Number of iterations completed successfully
        failed_count: Number of iterations that failed
        total_count: Total iterations launched for this step
        duration_seconds: End-to-end map-step wall-clock duration
        met_threshold: Whether configured success threshold was satisfied
    """
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
