"""Pipeline event signals.

Covers pipeline registry availability, embedding step lifecycle,
and domain verification step lifecycle.

Signals:
    pipeline.registry.unavailable — pipeline skipped; model deps unresolvable
    pipeline.execution.timed.out — execution exceeded configured timeout
    pipeline.deadlock.detected — no runnable steps and no pending tasks
    pipeline.execution.cancelled — execution cancelled by external trigger
    pipeline.step.model.deferred — runnable step deferred by model gate
    pipeline.model.gate.claimed — model gate claimed for step execution
    pipeline.model.gate.released — model gate released after terminal step outcome
    pipeline.model.gate.failure.release — failure boundary release marker
    pipeline.model.registry.lookup.failed — model_ref lookup failed in registry
    pipeline.dag.execution.completed — all DAG nodes reached terminal states
    pipeline.step.embedding.started — embedding step began
    pipeline.step.embedding.completed — embeddings retrieved successfully
    pipeline.step.embedding.failed — embedding request failed
    pipeline.step.domain.verification.started — domain verification began
    pipeline.step.domain.verification.completed — domain verification finished
"""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

# ========================================
# Pipeline Registry Events
# ========================================

PIPELINE_REGISTRY_UNAVAILABLE = "pipeline.registry.unavailable"
# ========================================
# Pipeline DAG Coordination Events
# ========================================

PIPELINE_EXECUTION_TIMED_OUT = "pipeline.execution.timed.out"
PIPELINE_DEADLOCK_DETECTED = "pipeline.deadlock.detected"
PIPELINE_EXECUTION_CANCELLED = "pipeline.execution.cancelled"
PIPELINE_STEP_MODEL_DEFERRED = "pipeline.step.model.deferred"
PIPELINE_MODEL_GATE_CLAIMED = "pipeline.model.gate.claimed"
PIPELINE_MODEL_GATE_RELEASED = "pipeline.model.gate.released"
PIPELINE_MODEL_GATE_RELEASED_ON_FAILURE = "pipeline.model.gate.failure.release"
PIPELINE_MODEL_REGISTRY_LOOKUP_FAILED = "pipeline.model.registry.lookup.failed"
PIPELINE_DAG_EXECUTION_COMPLETED = "pipeline.dag.execution.completed"

"""
Pipeline permanently skipped — required models missing after deferred retry.

Emitted once per unavailable pipeline after each registry load or reload.
∀ id: model deps unresolvable against current gateway catalogs + registered pipelines.

Payload: {
    "pipeline_id": str,    # Pipeline that could not be loaded
    "missing_models": list[str],  # Model IDs that were not found
}
"""

# ========================================
# Pipeline Step Events: Embedding
# ========================================

PIPELINE_STEP_EMBEDDING_STARTED = "pipeline.step.embedding.started"
"""
Pipeline embedding step started.
Emitted when a pipeline step begins fetching embeddings.

Payload: {
    "execution_id": str,
    "step_id": str,
    "model_id": str,
    "input_count": int,
}
"""

PIPELINE_STEP_EMBEDDING_COMPLETED = "pipeline.step.embedding.completed"
"""
Pipeline embedding step completed.
Emitted when embeddings are successfully retrieved.

Payload: {
    "execution_id": str,
    "step_id": str,
    "model_id": str,
    "input_count": int,
    "duration_ms": float,
    "embedding_dim": int,
}
"""

PIPELINE_STEP_EMBEDDING_FAILED = "pipeline.step.embedding.failed"
"""
Pipeline embedding step failed.
Emitted when embedding request fails.

Payload: {
    "execution_id": str,
    "step_id": str,
    "model_id": str,
    "input_count": int,
    "duration_ms": float,
    "error": str,
    "status_code": int | None,
}
"""

# ========================================
# Pipeline Step Events: Domain Verification
# ========================================

PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED = "pipeline.step.domain.verification.started"
"""
Pipeline domain verification step started.
Emitted when domain-specific verification begins for a domain.

Payload: {
    "execution_id": str,
    "step_id": str,
    "domain": str,
    "model_id": str,
    "statement_count": int,
}
"""

PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED = (
    "pipeline.step.domain.verification.completed"
)
"""
Pipeline domain verification step completed.
Emitted when domain-specific verification completes for a domain.

Payload: {
    "execution_id": str,
    "step_id": str,
    "domain": str,
    "model_id": str,
    "statement_count": int,
    "passed_count": int,
    "failed_count": int,
    "duration_ms": float,
}
"""


# ========================================
# Factory Functions
# ========================================


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
