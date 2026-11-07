"""Review-layer detection and materialization for API-substrate consult reviews."""

from __future__ import annotations

from review_layer.detect import (
    DetectionResult,
    ReviewTarget,
    cited_layer_ids,
    detect_review_targets,
    implicit_target_heads,
    resolve_primary_target,
)
from review_layer.layer_gc import gc_expired_layers
from review_layer.materialize import (
    ReviewLayerManifest,
    ReviewTargetUnreachable,
    any_cited_layer_present,
    format_review_layer_prompt_block,
    layer_uri,
    materialize_review_layer,
    review_layer_exists,
    validate_layer_id,
)

__all__ = [
    "DetectionResult",
    "ReviewLayerManifest",
    "ReviewTarget",
    "ReviewTargetUnreachable",
    "any_cited_layer_present",
    "cited_layer_ids",
    "detect_review_targets",
    "format_review_layer_prompt_block",
    "gc_expired_layers",
    "implicit_target_heads",
    "layer_uri",
    "materialize_review_layer",
    "resolve_primary_target",
    "review_layer_exists",
    "validate_layer_id",
]
