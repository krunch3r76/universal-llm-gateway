"""Request lifecycle event signals.

Covers the full request lifecycle from queue entry to completion/failure,
plus the federation snapshot emitted by Edge Stargate and model-selection
reputation signals.

Package-shadow of the former ``request.py`` module. Implementation lives in
responsibility-named submodules; this facade preserves
``from src.scheduling.events.request import …``.
"""

# ruff: noqa: N802

from .failure import (
    REQUEST_CLIENT_DISCONNECTED,
    REQUEST_DEADLINE_EXCEEDED,
    REQUEST_FAILED,
    REQUEST_REMOVED,
    REQUEST_TIMEOUT,
    RequestClientDisconnected,
    RequestDeadlineExceeded,
    RequestFailed,
    RequestRemoved,
    RequestTimeout,
)
from .federation_snapshot import (
    FEDERATION_SNAPSHOT_SENT,
    FederationSnapshotSent,
)
from .lifecycle import (
    REQUEST_ALIAS_RESOLVED,
    REQUEST_COMPLETED,
    REQUEST_INFERENCE_STARTED,
    REQUEST_PROCESSING,
    REQUEST_PROFILE_RESOLVED,
    REQUEST_QUEUED,
    RequestAliasResolved,
    RequestCompleted,
    RequestInferenceStarted,
    RequestProcessing,
    RequestProfileResolved,
    RequestQueued,
)
from .model_selection import (
    MODEL_SELECTION_HEALTH_OBSERVATION,
    MODEL_SELECTION_RANK_COMPUTED,
    MODEL_SELECTION_SCORE_UPDATED,
    MODEL_SELECTION_SWITCH_ALLOWED,
    MODEL_SELECTION_SWITCH_SUPPRESSED,
    ModelSelectionHealthObservation,
    ModelSelectionRankComputed,
    ModelSelectionScoreUpdated,
    ModelSelectionSwitchAllowed,
    ModelSelectionSwitchSuppressed,
)

__all__ = [
    "REQUEST_QUEUED",
    "REQUEST_PROCESSING",
    "REQUEST_INFERENCE_STARTED",
    "REQUEST_PROFILE_RESOLVED",
    "REQUEST_ALIAS_RESOLVED",
    "REQUEST_COMPLETED",
    "REQUEST_FAILED",
    "REQUEST_TIMEOUT",
    "REQUEST_DEADLINE_EXCEEDED",
    "REQUEST_CLIENT_DISCONNECTED",
    "REQUEST_REMOVED",
    "FEDERATION_SNAPSHOT_SENT",
    "MODEL_SELECTION_HEALTH_OBSERVATION",
    "MODEL_SELECTION_SCORE_UPDATED",
    "MODEL_SELECTION_RANK_COMPUTED",
    "MODEL_SELECTION_SWITCH_SUPPRESSED",
    "MODEL_SELECTION_SWITCH_ALLOWED",
    "RequestQueued",
    "RequestProcessing",
    "RequestProfileResolved",
    "RequestAliasResolved",
    "RequestInferenceStarted",
    "RequestCompleted",
    "RequestFailed",
    "RequestTimeout",
    "RequestDeadlineExceeded",
    "RequestClientDisconnected",
    "RequestRemoved",
    "FederationSnapshotSent",
    "ModelSelectionHealthObservation",
    "ModelSelectionScoreUpdated",
    "ModelSelectionRankComputed",
    "ModelSelectionSwitchSuppressed",
    "ModelSelectionSwitchAllowed",
]
