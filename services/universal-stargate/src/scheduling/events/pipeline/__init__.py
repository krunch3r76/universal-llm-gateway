"""Pipeline registry, gate, embedding, and domain-verification event signals.

Package-shadow of the former ``pipeline.py`` module; ``signal_constants`` and
``factories`` hold implementation. This facade preserves the historical import
surface.
"""

# ruff: noqa: N802

from .factories import (
    PipelineDagExecutionCompleted,
    PipelineDeadlockDetected,
    PipelineExecutionCancelled,
    PipelineExecutionTimedOut,
    PipelineModelGateClaimed,
    PipelineModelGateReleased,
    PipelineModelGateReleasedOnFailure,
    PipelineModelRegistryLookupFailed,
    PipelineStepEmbeddingCompleted,
    PipelineStepEmbeddingFailed,
    PipelineStepEmbeddingStarted,
    PipelineStepModelDeferred,
    pipeline_registry_unavailable,
    pipeline_step_domain_verification_completed,
    pipeline_step_domain_verification_started,
)
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

__all__ = [
    "PIPELINE_REGISTRY_UNAVAILABLE",
    "PIPELINE_EXECUTION_TIMED_OUT",
    "PIPELINE_DEADLOCK_DETECTED",
    "PIPELINE_EXECUTION_CANCELLED",
    "PIPELINE_STEP_MODEL_DEFERRED",
    "PIPELINE_MODEL_GATE_CLAIMED",
    "PIPELINE_MODEL_GATE_RELEASED",
    "PIPELINE_MODEL_GATE_RELEASED_ON_FAILURE",
    "PIPELINE_MODEL_REGISTRY_LOOKUP_FAILED",
    "PIPELINE_DAG_EXECUTION_COMPLETED",
    "PIPELINE_STEP_EMBEDDING_STARTED",
    "PIPELINE_STEP_EMBEDDING_COMPLETED",
    "PIPELINE_STEP_EMBEDDING_FAILED",
    "PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED",
    "PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED",
    "pipeline_registry_unavailable",
    "PipelineExecutionTimedOut",
    "PipelineDeadlockDetected",
    "PipelineExecutionCancelled",
    "PipelineStepModelDeferred",
    "PipelineModelGateClaimed",
    "PipelineModelGateReleased",
    "PipelineModelGateReleasedOnFailure",
    "PipelineModelRegistryLookupFailed",
    "PipelineDagExecutionCompleted",
    "PipelineStepEmbeddingStarted",
    "PipelineStepEmbeddingCompleted",
    "PipelineStepEmbeddingFailed",
    "pipeline_step_domain_verification_started",
    "pipeline_step_domain_verification_completed",
]
