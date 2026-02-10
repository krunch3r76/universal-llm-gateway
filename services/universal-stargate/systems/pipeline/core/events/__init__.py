"""
Pipeline execution events.

Event categories:
- Pipeline lifecycle: started, completed, failed
- Step lifecycle: started, completed, failed, skipped
- Map step progress: iteration tracking, timeout warnings
- Checkpoint operations: saved, loaded, failed

Event-driven state invariant:
- Pipeline/step state visible via events (single source of truth)
- External observers can track execution progress
- All events include pipeline_id and execution_id for correlation
"""

from .checkpoint import CheckpointFailed, CheckpointLoaded, CheckpointSaved
from .map import (
    MapIterationCompleted,
    MapIterationFailed,
    MapIterationStarted,
    MapStepCompleted,
    MapStepStarted,
    MapTimeoutWarning,
)
from .pipeline import (
    PipelineCancelled,
    PipelineCompleted,
    PipelineFailed,
    PipelineStarted,
)
from .step import StepCompleted, StepFailed, StepSkipped, StepStarted

__all__ = [
    # Checkpoint events
    "CheckpointSaved",
    "CheckpointLoaded",
    "CheckpointFailed",
    # Pipeline events
    "PipelineStarted",
    "PipelineCompleted",
    "PipelineFailed",
    "PipelineCancelled",
    # Step events
    "StepStarted",
    "StepCompleted",
    "StepFailed",
    "StepSkipped",
    # Map events
    "MapStepStarted",
    "MapIterationStarted",
    "MapIterationCompleted",
    "MapIterationFailed",
    "MapTimeoutWarning",
    "MapStepCompleted",
]
