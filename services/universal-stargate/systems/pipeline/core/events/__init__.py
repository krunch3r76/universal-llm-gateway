"""
Pipeline execution events.

Two event systems coexist during migration:
- Old bus events (@event_factory → EventBus): step.py, pipeline.py, map.py
- New observability events (dataclass → JSONL): lifecycle.py, verification.py

The old bus events are imported by DAGExecutor and MapExecutor with Bus* aliases.
The new dataclass events power the JSONL-based pipeline viewer.

Checkpoint events remain unchanged (not migrated).
"""

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
    StepFailed,
    StepInputsCaptured,
    StepOutputCaptured,
    StepSkipped,
    StepStarted,
)
from .recorder import EventRecorder
from .verification import (
    ClaimsClassified,
    ClaimsContextualized,
    ClaimsExtracted,
    CompoundClaimsDecomposed,
    DomainVerificationCompleted,
    EnrichReviewCompleted,
    FilterNegativesCompleted,
    ModelVerdictCast,
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
    # Lifecycle (new dataclass events for JSONL recorder)
    "PipelineStarted",
    "PipelineCompleted",
    "PipelineFailed",
    "PipelineCancelled",
    "StepStarted",
    "StepCompleted",
    "StepFailed",
    "StepSkipped",
    "StepInputsCaptured",
    "StepOutputCaptured",
    "MapIterationCompleted",
    # Inference
    "ModelInvocation",
    # Verification
    "ClaimsExtracted",
    "ClaimsClassified",
    "ClaimsContextualized",
    "CompoundClaimsDecomposed",
    "DomainVerificationCompleted",
    "EnrichReviewCompleted",
    "FilterNegativesCompleted",
    "ModelVerdictCast",
    "TiebreakerTriggered",
    "ThresholdApplied",
    "SynergizeCompleted",
    "VerificationComplete",
    "VetoPassCompleted",
    # Checkpoint
    "CheckpointSaved",
    "CheckpointLoaded",
    "CheckpointFailed",
]
