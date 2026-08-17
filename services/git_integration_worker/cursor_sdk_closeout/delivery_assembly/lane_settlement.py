"""Lane-B commit-on-terminal / branch state / three-plane probe / disposition; dispatch-record fields; tip-window meter and landed suppression.

All ``from services.git_integration_worker.cursor_sdk_* import ...`` blocks that
today sit inside ``_assemble_closeout_delivery``'s lane-B / ledger / meter
branches stay function-local in this module's function — they exist to break
import cycles. Do not hoist them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_git_head import (
    resolve_git_head,
    tip_window_meter_counts,
)

from ..closeout_records import SdkRunOutcome


def settle_lane_and_dispatch_fields(
    *,
    binding: CaptureBinding | None,
    dispatch_id: str,
    write_tree: Path,
    receipt_tree: Path,
    repo_change_set: ChangeSet,
    outcome: SdkRunOutcome,
    deviations: list[str],
    divergence_reason: str | None,
    baseline: dict[str, Any] | None,
    files_untracked_or_ignored: tuple[str, ...],
    offgit_uris: object,
    thread_id: str,
    gate_d_created_rels: tuple[str, ...],
) -> tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    int | None,
    int | None,
    bool | None,
    str | None,
    bool | None,
    str,
    bool,
    str | None,
    list[str],
    str | None,
]:
    lane_b_lane: str | None = None
    lane_b_branch: str | None = None
    lane_b_branch_point: str | None = None
    lane_b_head_sha: str | None = None
    lane_b_commits_ahead: int | None = None
    lane_b_landed: bool | None = None
    if binding is not None and binding.lane == "B":
        from services.git_integration_worker.cursor_sdk_lane_b_commit import (
            branch_state,
            commit_on_terminal,
        )
        from services.git_integration_worker.cursor_sdk_worktree import (
            lookup_dispatch_worktree,
        )

        record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
        if record is not None:
            commit_result = commit_on_terminal(
                dispatch_id=dispatch_id,
                worktree_path=write_tree,
                branch_name=record.branch_name,
            )
            state = branch_state(
                binding.receipt_tree,
                branch_name=record.branch_name,
                branch_point=record.branch_point,
            )
            if (
                commit_result.committed
                and commit_result.head_sha
                and state.commits_ahead is not None
            ):
                from services.git_integration_worker.cursor_sdk_events import (
                    emit_sdk_lane_b_committed,
                )

                files_committed = len(repo_change_set.created) + len(
                    repo_change_set.modified
                )
                emit_sdk_lane_b_committed(
                    dispatch_id=dispatch_id,
                    thread_id=thread_id,
                    head_sha=commit_result.head_sha,
                    commits_ahead=state.commits_ahead,
                    files_committed=files_committed,
                )
            if commit_result.refused:
                # Work exists in the worktree but git declined to record it; the
                # dispatch cannot be graded as shipped off an uncommitted tree.
                refusal_token = (
                    f"divergence:lane_b_commit_refused:{commit_result.short_error}"
                )
                deviations = [*(deviations or []), refusal_token]
                if divergence_reason is None:
                    divergence_reason = "divergence:lane_b_commit_refused"
            lane_b_lane = "B"
            lane_b_branch = record.branch_name
            lane_b_branch_point = record.branch_point
            lane_b_head_sha = state.head_sha
            lane_b_commits_ahead = state.commits_ahead
            # landed@local-master — ancestry probe + G₂ meter; unknown stays None.
            from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
                probe_three_planes,
            )

            plane_obs = probe_three_planes(
                binding.receipt_tree,
                head_sha=state.head_sha,
                branch=record.branch_name,
            )
            # G₂: measured 0 refuses vacuous True; unknown ancestry/meter → None.
            from services.git_integration_worker.cursor_sdk_deliverables_expected import (
                admit_landed_true,
            )

            lane_b_landed = admit_landed_true(
                ancestry_on_master=plane_obs.landed_local_master,
                commits_ahead=state.commits_ahead,
            )
            if outcome.status != "finished" and not state.safe_to_delete:
                from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
                    mark_lane_b_disposition,
                )

                mark_lane_b_disposition(
                    branch_name=record.branch_name,
                    reason="abandoned",
                    dispatch_id=dispatch_id,
                    tip_sha=state.head_sha,
                )
    reported_lane = binding.lane if binding is not None else None
    isolation_mat: bool | None = None
    escalation_harvest: str = "none"
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT record_json, lease_key, source_repo FROM cursor_sdk_dispatches "
            "WHERE dispatch_id = ?",
            (dispatch_id,),
        ).fetchone()
    if row is not None:
        from services.git_integration_worker.cursor_sdk_capacity_invariant import (
            resolve_isolation_materialized,
        )

        isolation_mat = resolve_isolation_materialized(
            record_json=row["record_json"],
            lease_key=row["lease_key"],
            source_repo=row["source_repo"],
        )
        try:
            record_data = json.loads(row["record_json"] or "{}")
        except json.JSONDecodeError:
            record_data = {}
        raw_harvest = record_data.get("escalation_harvest")
        if isinstance(raw_harvest, str) and raw_harvest.strip():
            escalation_harvest = raw_harvest.strip()
    cortex_authoritative = bool(gate_d_created_rels)
    closeout_head = resolve_git_head(write_tree)
    # Lane-A: populate capture head_sha from write-tree tip when Lane-B did not
    # assign one — keys the three-plane probe without upgrading from checkpoint prose.
    capture_head_sha = lane_b_head_sha if lane_b_head_sha is not None else closeout_head
    # Lane-A: populate commits_ahead from admit_head..closeout_head (symmetric with
    # Lane-B branch_point..branch). A real admit_head with an empty range is a
    # measured 0 (refuse vacuous landed). A missing/unresolvable admit_head must
    # leave the key absent — never launder None into 0 (presence typing travels).
    capture_commits_ahead = lane_b_commits_ahead
    capture_commits_ahead_unfiltered: int | None = None
    capture_landed = lane_b_landed
    if capture_commits_ahead is None:
        admit_head: str | None = None
        if isinstance(baseline, dict):
            raw_admit = baseline.get("admit_head")
            if isinstance(raw_admit, str) and raw_admit.strip():
                admit_head = raw_admit.strip()
        if admit_head is not None and closeout_head is not None:
            meter_pair = tip_window_meter_counts(
                write_tree,
                dispatch_id=dispatch_id,
                admit_head=admit_head,
                closeout_head=closeout_head,
            )
            if meter_pair is not None:
                capture_commits_ahead, capture_commits_ahead_unfiltered = meter_pair
        if lane_b_lane != "B" and capture_commits_ahead is not None:
            from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
                probe_three_planes,
            )
            from services.git_integration_worker.cursor_sdk_deliverables_expected import (
                admit_landed_true,
            )

            plane_obs = probe_three_planes(
                receipt_tree,
                head_sha=capture_head_sha,
                branch=lane_b_branch,
            )
            capture_landed = admit_landed_true(
                ancestry_on_master=plane_obs.landed_local_master,
                commits_ahead=capture_commits_ahead,
            )
    from services.git_integration_worker.cursor_sdk_deliverables_expected import (
        git_land_plane_uncomputable,
        suppress_vacuous_git_landed,
    )

    capture_landed = suppress_vacuous_git_landed(
        capture_landed,
        uncomputable=git_land_plane_uncomputable(
            created=repo_change_set.created,
            modified=repo_change_set.modified,
            deleted=repo_change_set.deleted,
            untracked=files_untracked_or_ignored,
            offgit=offgit_uris,
        ),
    )
    return (
        lane_b_lane, lane_b_branch, lane_b_branch_point, capture_head_sha,
        capture_commits_ahead, capture_commits_ahead_unfiltered, capture_landed,
        reported_lane, isolation_mat, escalation_harvest, cortex_authoritative,
        closeout_head, deviations, divergence_reason,
    )
