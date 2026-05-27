"""Step-level model fallback bus event factories.

Callers: DAGExecutor step_model_fallback handler, dag_executor executor.
Covers fallback attempt and suppression events for step-level model switching.
Signals in namespace pipeline.step.model.fallback.*.
"""

from universal_event_bus import Event, event_factory


@event_factory
def StepModelFallback(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    primary_model: str,
    fallback_model: str,
    primary_error_type: str,
    fallback_attempt: int,
    total_fallbacks: int,
    succeeded: bool,
) -> Event:
    """Emitted when step-level model fallback is attempted or resolves.

    Fires at the executor level after the full retry chain exhausts
    for the primary model. Covers all failure types: timeout, proxy error,
    handler error.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        primary_model: Initially selected model ID
        fallback_model: Fallback model attempted
        primary_error_type: Classified primary failure type
        fallback_attempt: 1-based fallback attempt index
        total_fallbacks: Total fallback candidates available
        succeeded: True when fallback invocation succeeded
    """
    return Event(
        signal="pipeline.step.model.fallback",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "primary_error_type": primary_error_type,
            "fallback_attempt": fallback_attempt,
            "total_fallbacks": total_fallbacks,
            "succeeded": succeeded,
        },
    )


@event_factory
def StepModelFallbackSuppressed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    primary_error_type: str,
    suppression_reason: str,
) -> Event:
    """Emitted when step-level fallback is intentionally not attempted.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        primary_error_type: Error class from primary model attempt
        suppression_reason: Why fallback was suppressed
    """
    return Event(
        signal="pipeline.step.model.fallback.suppressed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "primary_error_type": primary_error_type,
            "suppression_reason": suppression_reason,
        },
    )
