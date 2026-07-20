"""Factory functions for `pipeline` scheduling events. Builds `Event` objects via `event_factory` from this package's signal constants (DAG execution completion, deadlock detection, cancellation/timeout, model-gate claim/release, registry lookup failures, and per-step embedding/domain-verification progress) for callers importing from `src.scheduling.events.pipeline`."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

from .signal_constants import (
    PIPELINE_DAG_EXECUTION_COMPLETED,
    PIPELINE_DEADLOCK_DETECTED,
    PIPELINE_EXECUTION_CANCELLED,
    PIPELINE_EXECUTION_TIMED_OUT,
    PIPELINE_MODEL_GATE_CLAIMED,
    PIPELINE_MODEL_GATE_RELEASED,
    PIPELINE_MODEL_GATE_RELEASED_ON_FAILURE,
    PIPELINE_MODEL_REGISTRY_LOOKUP_FAILED,
    PIPELINE_REGISTRY_UNAVAILABLE,
    PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED,
    PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED,
    PIPELINE_STEP_EMBEDDING_COMPLETED,
    PIPELINE_STEP_EMBEDDING_FAILED,
    PIPELINE_STEP_EMBEDDING_STARTED,
    PIPELINE_STEP_MODEL_DEFERRED,
)


@event_factory
def pipeline_registry_unavailable(
    pipeline_id: str,
    missing_models: list[str],
) -> Event:
    """
    Pipeline permanently skipped after deferred retry — model deps unresolvable.

    Payload:
        pipeline_id: Pipeline ID that could not be loaded
        missing_models: Model IDs absent from all gateway catalogs and pipeline registry
    """
    return Event(
        signal=PIPELINE_REGISTRY_UNAVAILABLE,
        payload={
            "pipeline_id": pipeline_id,
            "missing_models": missing_models,
        },
    )


@event_factory
def PipelineExecutionTimedOut(
    pipeline_id: str,
    execution_id: str,
    timeout_seconds: float,
    incomplete_steps: list[str],
) -> Event:
    """Emit timeout boundary for DAG execution with pending step identifiers."""
    return Event(
        signal=PIPELINE_EXECUTION_TIMED_OUT,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "timeout_seconds": timeout_seconds,
            "incomplete_steps": incomplete_steps,
        },
    )


@event_factory
def PipelineDeadlockDetected(
    pipeline_id: str,
    execution_id: str,
    incomplete_steps: list[str],
    pending_task_count: int,
) -> Event:
    """Emit deadlock boundary when scheduler can no longer make forward progress."""
    return Event(
        signal=PIPELINE_DEADLOCK_DETECTED,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "incomplete_steps": incomplete_steps,
            "pending_task_count": pending_task_count,
        },
    )


@event_factory
def PipelineExecutionCancelled(
    pipeline_id: str,
    execution_id: str,
    cancelled_steps: list[str],
) -> Event:
    """Emit cancellation summary when an execution is externally cancelled."""
    return Event(
        signal=PIPELINE_EXECUTION_CANCELLED,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "cancelled_steps": cancelled_steps,
        },
    )


@event_factory
def PipelineStepModelDeferred(
    pipeline_id: str,
    execution_id: str,
    step_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Emit step deferral due to model admission-gate constraints."""
    return Event(
        signal=PIPELINE_STEP_MODEL_DEFERRED,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def PipelineModelGateClaimed(
    pipeline_id: str,
    execution_id: str,
    step_id: str,
    model_id: str,
) -> Event:
    """Emit model gate claim acquisition for a step transition to running."""
    return Event(
        signal=PIPELINE_MODEL_GATE_CLAIMED,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
        },
    )


@event_factory
def PipelineModelGateReleased(
    pipeline_id: str,
    execution_id: str,
    step_id: str,
    model_id: str,
    outcome: str,
) -> Event:
    """Emit model gate release for success, failure, or cancellation outcomes."""
    return Event(
        signal=PIPELINE_MODEL_GATE_RELEASED,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "outcome": outcome,
        },
    )


@event_factory
def PipelineModelGateReleasedOnFailure(
    pipeline_id: str,
    execution_id: str,
    step_id: str,
    model_id: str,
    error_type: str,
) -> Event:
    """Emit explicit failure-boundary release marker for model gate observability."""
    return Event(
        signal=PIPELINE_MODEL_GATE_RELEASED_ON_FAILURE,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "error_type": error_type,
        },
    )


@event_factory
def PipelineModelRegistryLookupFailed(
    pipeline_id: str,
    execution_id: str,
    step_id: str,
    model_ref: str,
    error: str,
) -> Event:
    """Emit registry lookup failure for a step model_ref before execution launch."""
    return Event(
        signal=PIPELINE_MODEL_REGISTRY_LOOKUP_FAILED,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_id": step_id,
            "model_ref": model_ref,
            "error": error,
        },
    )


@event_factory
def PipelineDagExecutionCompleted(
    pipeline_id: str,
    execution_id: str,
    completed_count: int,
    skipped_count: int,
    failed_count: int,
    total_steps: int,
) -> Event:
    """Emit terminal DAG completion summary after all steps settle."""
    return Event(
        signal=PIPELINE_DAG_EXECUTION_COMPLETED,
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "completed_count": completed_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "total_steps": total_steps,
        },
    )


@event_factory
def PipelineStepEmbeddingStarted(
    execution_id: str,
    step_id: str,
    model_id: str,
    input_count: int,
) -> Event:
    """
    Create PIPELINE_STEP_EMBEDDING_STARTED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        model_id: Embedding model being used
        input_count: Number of texts to embed

    Returns:
        Event with PipelineStepEmbeddingStarted signal
    """
    return Event(
        signal=PIPELINE_STEP_EMBEDDING_STARTED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "input_count": input_count,
        },
    )


@event_factory
def PipelineStepEmbeddingCompleted(
    execution_id: str,
    step_id: str,
    model_id: str,
    input_count: int,
    duration_ms: float,
    embedding_dim: int,
) -> Event:
    """
    Create PIPELINE_STEP_EMBEDDING_COMPLETED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        model_id: Embedding model used
        input_count: Number of texts embedded
        duration_ms: Time taken in milliseconds
        embedding_dim: Dimension of embeddings

    Returns:
        Event with PipelineStepEmbeddingCompleted signal
    """
    return Event(
        signal=PIPELINE_STEP_EMBEDDING_COMPLETED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "input_count": input_count,
            "duration_ms": duration_ms,
            "embedding_dim": embedding_dim,
        },
    )


@event_factory
def PipelineStepEmbeddingFailed(
    execution_id: str,
    step_id: str,
    model_id: str,
    input_count: int,
    duration_ms: float,
    error: str,
    status_code: int | None = None,
) -> Event:
    """
    Create PIPELINE_STEP_EMBEDDING_FAILED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        model_id: Embedding model attempted
        input_count: Number of texts attempted
        duration_ms: Time taken before failure
        error: Error message
        status_code: HTTP status code if applicable

    Returns:
        Event with PipelineStepEmbeddingFailed signal
    """
    return Event(
        signal=PIPELINE_STEP_EMBEDDING_FAILED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "input_count": input_count,
            "duration_ms": duration_ms,
            "error": error,
            "status_code": status_code,
        },
    )


@event_factory
def pipeline_step_domain_verification_started(
    execution_id: str,
    step_id: str,
    domain: str,
    model_id: str,
    statement_count: int,
) -> Event:
    """
    Create PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        domain: Domain being verified (e.g., "mathematics")
        model_id: Domain authority model
        statement_count: Number of statements to verify

    Returns:
        Event with PipelineStepDomainVerificationStarted signal
    """
    return Event(
        signal=PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "domain": domain,
            "model_id": model_id,
            "statement_count": statement_count,
        },
    )


@event_factory
def pipeline_step_domain_verification_completed(
    execution_id: str,
    step_id: str,
    domain: str,
    model_id: str,
    statement_count: int,
    passed_count: int,
    failed_count: int,
    duration_ms: float,
) -> Event:
    """
    Create PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        domain: Domain that was verified
        model_id: Domain authority model used
        statement_count: Number of statements verified
        passed_count: Statements that passed verification
        failed_count: Statements that failed verification
        duration_ms: Time taken in milliseconds

    Returns:
        Event with PipelineStepDomainVerificationCompleted signal
    """
    return Event(
        signal=PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "domain": domain,
            "model_id": model_id,
            "statement_count": statement_count,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "duration_ms": duration_ms,
        },
    )
