"""Review-layer detection and materialization for API-substrate consult reviews."""

from __future__ import annotations

from review_layer.detect import ReviewTarget, detect_review_targets
from review_layer.materialize import (
    ReviewLayerManifest,
    ReviewTargetUnreachable,
    gc_expired_layers,
    materialize_review_layer,
)

__all__ = [
    "ReviewLayerManifest",
    "ReviewTarget",
    "ReviewTargetUnreachable",
    "detect_review_targets",
    "gc_expired_layers",
    "materialize_review_layer",
]
