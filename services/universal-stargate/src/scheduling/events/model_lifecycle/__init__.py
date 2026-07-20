"""Model lifecycle event signals.

Package-shadow of the former ``model_lifecycle.py`` module. Covers loaded/unloaded,
loading progress/failure, execution, capacity free, worker eviction, and
availability.
"""

# ruff: noqa: N802

from .factories import (
    ModelAvailable,
    ModelCapacityFreed,
    ModelExecutionCompleted,
    ModelExecutionFailed,
    ModelExecutionStarted,
    ModelLoaded,
    ModelLoadingFailed,
    ModelLoadingProgress,
    ModelLoadingStarted,
    ModelLoadingStuck,
    ModelUnavailable,
    ModelUnloaded,
    WorkerEvicted,
)
from .signal_constants import (
    MODEL_AVAILABLE,
    MODEL_CAPACITY_FREED,
    MODEL_EXECUTION_COMPLETED,
    MODEL_EXECUTION_FAILED,
    MODEL_EXECUTION_STARTED,
    MODEL_LOAD_FAILED,
    MODEL_LOADED,
    MODEL_LOADING_PROGRESS,
    MODEL_LOADING_STARTED,
    MODEL_LOADING_STUCK,
    MODEL_UNAVAILABLE,
    MODEL_UNLOADED,
    WORKER_EVICTED,
)

__all__ = [
    "MODEL_LOADED",
    "MODEL_UNLOADED",
    "MODEL_LOADING_STARTED",
    "MODEL_LOADING_PROGRESS",
    "MODEL_LOAD_FAILED",
    "MODEL_LOADING_STUCK",
    "MODEL_EXECUTION_STARTED",
    "MODEL_EXECUTION_COMPLETED",
    "MODEL_EXECUTION_FAILED",
    "MODEL_CAPACITY_FREED",
    "WORKER_EVICTED",
    "MODEL_AVAILABLE",
    "MODEL_UNAVAILABLE",
    "ModelLoaded",
    "ModelUnloaded",
    "ModelAvailable",
    "ModelUnavailable",
    "ModelLoadingStarted",
    "ModelLoadingProgress",
    "ModelLoadingFailed",
    "ModelLoadingStuck",
    "ModelExecutionStarted",
    "ModelExecutionCompleted",
    "ModelExecutionFailed",
    "ModelCapacityFreed",
    "WorkerEvicted",
]
