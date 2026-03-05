"""
Pipeline execution events.

Two event systems coexist during migration:
- Old bus events (@event_factory → EventBus): step.py, pipeline.py, map.py
- New observability events (dataclass → JSONL): lifecycle.py, verification.py

The old bus events are imported by DAGExecutor and MapExecutor with Bus* aliases.
The new dataclass events power the JSONL-based pipeline viewer.

Checkpoint events remain unchanged (not migrated).
"""

from .assess_loop import (
    AssessLoopCompleted,
    AssessLoopIterationCompleted,
    AssessLoopStarted,
)
from .base import PipelineEvent
from .checkpoint import CheckpointFailed, CheckpointLoaded, CheckpointSaved
from .inference import ModelInvocation
from .lifecycle import (
    MapIterationCompleted,
    PipelineCancelled,
    PipelineCompleted,
    PipelineFailed,
    PipelineStarted,
    StepCompleted,
    StepConditionEvaluated,
    StepFailed,
    StepInputsCaptured,
    StepModelResolved,
    StepOutputCaptured,
    StepProgress,
    StepSkipped,
    StepStarted,
)
from .recorder import EventRecorder
from .step import RagRetrievalParamsResolved
from .verification import (
    ClaimsClassified,
    ClaimsContextualized,
    ClaimsExtracted,
    CombinePassagesCompleted,
    CompoundClaimsDecomposed,
    CoverageAuditCompleted,
    DomainVerificationCompleted,
    EnrichReviewCompleted,
    FilterNegativesCompleted,
    ModelVerdictCast,
    OrganizeFactsCompleted,
    SynergizeCompleted,
    ThresholdApplied,
    TiebreakerTriggered,
    VerificationComplete,
    VetoPassCompleted,
)

__all__ = [
    # Base
    "PipelineEvent",
    "EventRecorder",
    # Assess loop
    "AssessLoopStarted",
    "AssessLoopIterationCompleted",
    "AssessLoopCompleted",
    # Lifecycle (new dataclass events for JSONL recorder)
    "PipelineStarted",
    "PipelineCompleted",
    "PipelineFailed",
    "PipelineCancelled",
    "StepStarted",
    "StepCompleted",
    "StepFailed",
    "StepProgress",
    "StepSkipped",
    "StepConditionEvaluated",
    "StepInputsCaptured",
    "StepModelResolved",
    "StepOutputCaptured",
    "MapIterationCompleted",
    # Inference
    "ModelInvocation",
    # Verification
    "CombinePassagesCompleted",
    "CoverageAuditCompleted",
    "ClaimsExtracted",
    "ClaimsClassified",
    "ClaimsContextualized",
    "CompoundClaimsDecomposed",
    "DomainVerificationCompleted",
    "EnrichReviewCompleted",
    "FilterNegativesCompleted",
    "ModelVerdictCast",
    "OrganizeFactsCompleted",
    "TiebreakerTriggered",
    "ThresholdApplied",
    "SynergizeCompleted",
    "VerificationComplete",
    "VetoPassCompleted",
    # RAG
    "RagRetrievalParamsResolved",
    # Checkpoint
    "CheckpointSaved",
    "CheckpointLoaded",
    "CheckpointFailed",
]
