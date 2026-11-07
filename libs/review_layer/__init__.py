"""Review-target detection and lane fs guidance for API-substrate consult reviews."""

from __future__ import annotations

from review_layer.detect import (
    ReviewTarget,
    detect_review_targets,
    distinct_explicit_review_threads,
    implicit_lane_thread_ids,
    resolve_primary_target,
)
from review_layer.git_io import (
    ReviewLayerInfraError,
    ReviewTargetUnreachable,
    hub_repo_path,
)
from review_layer.materialize import (
    default_lane_branch,
    format_review_target_block,
    lane_worktree_path,
    resolve_review_target,
    worktree_root,
)

__all__ = [
    "ReviewLayerInfraError",
    "ReviewTarget",
    "ReviewTargetUnreachable",
    "default_lane_branch",
    "detect_review_targets",
    "distinct_explicit_review_threads",
    "format_review_target_block",
    "hub_repo_path",
    "implicit_lane_thread_ids",
    "lane_worktree_path",
    "resolve_primary_target",
    "resolve_review_target",
    "worktree_root",
]
