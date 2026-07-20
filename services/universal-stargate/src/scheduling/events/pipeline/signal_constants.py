"""Signal string constants for `pipeline` scheduling events (DAG execution, deadlock detection, cancellation/timeout, model-gate claim/release, registry lookup failures, and step-level embedding/domain-verification progress). Re-exported via the `pipeline` package facade for `factories.py` and event subscribers."""

# ruff: noqa: N802

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
