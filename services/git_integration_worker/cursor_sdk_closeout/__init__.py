"""Public closeout surface for cursor-sdk worker dispatches (package-shadow).

Re-exports only consumer-imported names plus ``PostWaitSnapshot`` (return type
of exported ``read_post_wait_snapshot``). No external import block. No logic.
In-package code must not import names from this module.
"""

from .bus_body_budget import MAX_TURN_BODY_CHARS, finalize_closeout_body
from .closeout_records import CloseoutDelivery, PostWaitSnapshot, SdkRunOutcome
from .completion_outcome import (
    format_delivery_fallback_body,
    resolve_completion_outcome,
    resolve_run_outcome_label,
)
from .degraded_reasons import (
    degraded_implement_reason,
    degraded_reasons_from_exception,
    empty_assistant_turn_reason,
    empty_output_degraded_reason,
    merge_degraded_reasons,
)
from .deliverable_probe import verify_deliverables
from .delivery_prep import prepare_closeout_delivery, prepare_closeout_delivery_async
from .implement_body import build_implement_closeout_body
from .lint_verification import run_giw_subtree_f821_lint, run_touched_files_lint
from .post_wait_observation import (
    count_tool_calls,
    read_post_wait_snapshot,
    stream_only_effect_deviations,
)
from .sdk_git_snapshot import extract_sdk_git_snapshot, sdk_fs_git_mismatch_reason
from .worktree_baseline import (
    capture_wt_baseline,
    capture_wt_baseline_with_hashes,
    changed_paths,
    reconcile_workspace_changes,
)

__all__ = [
    "MAX_TURN_BODY_CHARS",
    "SdkRunOutcome",
    "PostWaitSnapshot",
    "CloseoutDelivery",
    "capture_wt_baseline",
    "capture_wt_baseline_with_hashes",
    "changed_paths",
    "reconcile_workspace_changes",
    "run_touched_files_lint",
    "run_giw_subtree_f821_lint",
    "verify_deliverables",
    "count_tool_calls",
    "read_post_wait_snapshot",
    "stream_only_effect_deviations",
    "merge_degraded_reasons",
    "degraded_reasons_from_exception",
    "degraded_implement_reason",
    "empty_output_degraded_reason",
    "empty_assistant_turn_reason",
    "extract_sdk_git_snapshot",
    "sdk_fs_git_mismatch_reason",
    "finalize_closeout_body",
    "build_implement_closeout_body",
    "prepare_closeout_delivery",
    "prepare_closeout_delivery_async",
    "format_delivery_fallback_body",
    "resolve_run_outcome_label",
    "resolve_completion_outcome",
]
