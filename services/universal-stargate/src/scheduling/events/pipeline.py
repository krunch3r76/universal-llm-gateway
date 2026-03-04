"""Pipeline event signals.

Covers pipeline registry availability, embedding step lifecycle,
and domain verification step lifecycle.

Signals:
    pipeline.registry.unavailable — pipeline skipped; model deps unresolvable
    pipeline.step.embedding.started — embedding step began
    pipeline.step.embedding.completed — embeddings retrieved successfully
    pipeline.step.embedding.failed — embedding request failed
    pipeline.step.domain.verification.started — domain verification began
    pipeline.step.domain.verification.completed — domain verification finished
"""

from universal_event_bus import Event, event_factory

# ========================================
# Pipeline Registry Events
# ========================================

PIPELINE_REGISTRY_UNAVAILABLE = "pipeline.registry.unavailable"
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
