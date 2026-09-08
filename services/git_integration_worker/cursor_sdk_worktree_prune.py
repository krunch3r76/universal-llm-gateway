"""Lane-B worktree prune-on-terminal and orphan reaper (S1b/S3/S6)."""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_branch_retained,
    emit_sdk_lane_b_reap_skipped_live_bridge,
    emit_sdk_lane_b_reaped,
    emit_sdk_lane_b_registry_ghost_row,
    emit_sdk_lane_b_salvaged,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    branch_state,
    is_worktree_dirty,
)
from services.git_integration_worker.cursor_sdk_worktree_gc import (
    _delete_orphan_branch,
    gc_merged_dispatch_branches,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    containing_worktree_under_root,
    ledger_connection,
    live_bridge_worktree_paths,
    live_ledger_worktree_paths,
)
from services.git_integration_worker.cursor_sdk_worktree_reconcile import (
    reconcile_unregistered_worktrees,
    sweep_vanished_pinned_worktrees,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    active_pin,
    list_registered_worktrees_with_status,
    lookup_dispatch_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_release import (
    ReleaseRefusal,
    release_lane_worktree,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
_REAPABLE_STATUSES = frozenset({"completed", "failed", "cancelled"})
_LIVE_DISPATCH_STATUSES = ("admitted", "running", "queued", "parked_waiting")
_GHOST_EMIT_BUDGET = 20
_ghost_rows_reported: set[str] = set()
_ghost_rows_emitted = 0


def reset_ghost_row_reports() -> None:
    """Clear process-local ghost-row report memory (tests only)."""
    global _ghost_rows_emitted
    _ghost_rows_reported.clear()
    _ghost_rows_emitted = 0


@dataclass(frozen=True, slots=True)
class PruneResult:
    """Outcome of pruning a dispatch worktree (S3 non-destructive branch retention)."""

    pruned: bool
    branch_retained: bool = False
    salvaged: bool = False
    head_sha: str | None = None
    salvage_refused: bool = False


@dataclass(frozen=True, slots=True)
class ReapSweepResult:
    """Aggregate orphan reaper sweep (S6 salvage + prune + merged branch GC)."""

    reaped: int = 0
    salvaged: int = 0
    branches_retained: int = 0
    branches_gc: int = 0
    stale_metadata_pruned: bool = False
    salvage_refused: int = 0
    debts_escalated: int = 0
    debts_reconciled: int = 0
    worktrees_reconciled: int = 0
    worktrees_surfaced: int = 0
    registry_ghost_rows: int = 0
    live_bridge_holds: int = 0
    vanished_pins: int = 0


def is_reapable_dispatch_status(status: str | None) -> bool:
    """True when a dispatch row is an explicit terminal token."""
    return status in _REAPABLE_STATUSES


def sibling_non_terminal_dispatch_on_thread(
    *,
    thread_id: str,
    exclude_dispatch_id: str,
) -> str | None:
    """Return a sibling dispatch id on ``thread_id`` that is still non-terminal."""
    placeholders = ", ".join("?" for _ in _LIVE_DISPATCH_STATUSES)
    with ledger_connection() as conn:
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches "
            f"WHERE thread_id=? AND dispatch_id!=? AND status IN ({placeholders}) "
            "LIMIT 1",
            (thread_id, exclude_dispatch_id, *_LIVE_DISPATCH_STATUSES),
        ).fetchone()
    if row is None:
        return None
    return str(row["dispatch_id"])


def rollback_dispatch_worktree(
    *,
    dispatch_id: str,
    thread_id: str,
    source_repo: Path,
) -> PruneResult:
    """Remove a freshly minted worktree when post-mint admit fails."""
    sibling = sibling_non_terminal_dispatch_on_thread(
        thread_id=thread_id,
        exclude_dispatch_id=dispatch_id,
    )
    if sibling is not None:
        logger.warning(
            "lane_b rollback refused — sibling dispatch holds thread "
            "dispatch_id=%s thread_id=%s sibling=%s",
            dispatch_id,
            thread_id,
            sibling,
        )
        return PruneResult(pruned=False, branch_retained=True)
    return prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
        remove_trigger="rollback",
        allow_unharvested=True,
    )


def prune_dispatch_worktree(
    *,
    dispatch_id: str,
    source_repo: Path,
    remove_trigger: str | None = "reap",
    allow_unharvested: bool = False,
) -> PruneResult:
    """Remove a registered dispatch worktree via the release chokepoint."""
    record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    if record is None:
        return PruneResult(pruned=False)
    branch = record.branch_name
    branch_point = record.branch_point
    thread_id = record.thread_id or dispatch_id
    if remove_trigger is None:
        return PruneResult(pruned=False)
    release = release_lane_worktree(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        reason=remove_trigger,
        allow_unharvested=allow_unharvested,
    )
    if release.refusal == ReleaseRefusal.UNHARVESTED:
        return PruneResult(pruned=False, branch_retained=True)
    if release.salvage_refused:
        return PruneResult(
            pruned=False,
            branch_retained=True,
            salvage_refused=True,
            head_sha=release.head_sha,
        )
    if not release.released:
        branch_retained = release.branch_retained or release.refusal in (
            ReleaseRefusal.LIVE_BRIDGE,
            ReleaseRefusal.UNHARVESTED,
            ReleaseRefusal.RETAIN_ACTIVE,
            ReleaseRefusal.DISPATCH_ACTIVE,
            ReleaseRefusal.FOREIGN_LOCK,
        )
        return PruneResult(
            pruned=False,
            branch_retained=branch_retained,
            head_sha=release.head_sha,
            salvage_refused=release.salvage_refused,
        )
    state = branch_state(
        source_repo.resolve(),
        branch_name=branch,
        branch_point=branch_point,
    )
    branch_retained = release.branch_retained or not state.safe_to_delete
    if release.salvaged:
        emit_sdk_lane_b_salvaged(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            head_sha=release.head_sha or state.head_sha or "",
            trigger="reaper",
        )
    branch_deleted = False
    if state.safe_to_delete:
        branch_deleted = _delete_orphan_branch(
            repo=source_repo.resolve(),
            branch_name=branch,
            reason="prune_terminal",
            dispatch_id=dispatch_id,
            tip_sha=state.head_sha,
        )
        if not branch_deleted:
            branch_retained = True
    if branch_retained:
        emit_sdk_lane_b_branch_retained(
            dispatch_id=dispatch_id,
            branch=branch,
            commits_ahead=state.commits_ahead,
        )
    if not branch_deleted:
        emit_sdk_lane_b_reaped(
            dispatch_id=dispatch_id,
            branch_deleted=False,
            branch=None,
            tip_sha=None,
            reason=None,
        )
    return PruneResult(
        pruned=True,
        branch_retained=branch_retained,
        salvaged=release.salvaged,
        head_sha=state.head_sha,
    )


def maybe_prune_worktree_on_terminal(
    *,
    dispatch_id: str,
    source_repo: Path,
) -> PruneResult:
    """Lane trees outlive dispatches; terminal is not a prune trigger."""
    _ = dispatch_id, source_repo
    return PruneResult(pruned=False)


def active_managed_worktree_paths(*, worktree_root: Path) -> set[str]:
    """Worktree paths under ``worktree_root`` that no sweep may remove."""
    root = worktree_root.resolve()
    active: set[str] = set()
    with ledger_connection() as conn:
        rows = conn.execute(
            "SELECT lease_key, source_repo, status FROM cursor_sdk_dispatches "
            "WHERE status IN ('admitted','running','queued','parked_waiting')"
        ).fetchall()
    for row in rows:
        key = row["lease_key"] or row["source_repo"]
        if not key:
            continue
        path = containing_worktree_under_root(path=key, worktree_root=root)
        if path is not None:
            active.add(path)
    active |= live_ledger_worktree_paths(worktree_root=worktree_root)
    active |= live_bridge_worktree_paths(worktree_root=worktree_root)
    return active


def _git_worktree_prune(*, source_repo: Path) -> bool:
    """Drop stale worktree metadata after hand-deleted directories (S6)."""
    proc = subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "worktree", "prune"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning(
            "git worktree prune failed repo=%s err=%s",
            source_repo,
            proc.stderr.strip(),
        )
        return False
    return True


def reap_orphan_worktrees(
    *,
    source_repo: Path,
    worktree_root: Path,
) -> ReapSweepResult:
    """Salvage, drop, and GC orphan Lane-B worktrees (S6 reaper sweep)."""
    reaped = 0
    salvaged = 0
    branches_retained = 0
    salvage_refused = 0
    active = active_managed_worktree_paths(worktree_root=worktree_root)
    held = live_bridge_worktree_paths(worktree_root=worktree_root)
    vanished = sweep_vanished_pinned_worktrees(source_repo=source_repo)
    rows = list_registered_worktrees_with_status()
    for row in rows:
        wt_path = str(Path(row["worktree_path"]).resolve())
        status = row["status"]
        if wt_path in held:
            emit_sdk_lane_b_reap_skipped_live_bridge(
                worktree_path=wt_path,
                dispatch_id=row["dispatch_id"],
                stage="reap",
            )
            continue
        if wt_path in active:
            continue
        if not is_reapable_dispatch_status(status):
            continue
        thread_id = str(row["thread_id"] or "")
        pin = active_pin(source_repo=source_repo, thread_id=thread_id) if thread_id else None
        if pin is not None:
            result = prune_dispatch_worktree(
                dispatch_id=row["dispatch_id"] or thread_id,
                source_repo=source_repo,
            )
            if result.salvage_refused:
                salvage_refused += 1
            continue
        wt = Path(row["worktree_path"])
        if wt.is_dir() and is_worktree_dirty(wt):
            continue
        state = branch_state(
            source_repo.resolve(),
            branch_name=row["branch_name"],
            branch_point=row["branch_point"],
        )
        if not state.safe_to_delete:
            continue
        result = prune_dispatch_worktree(
            dispatch_id=row["dispatch_id"] or thread_id,
            source_repo=source_repo,
        )
        if result.salvage_refused:
            salvage_refused += 1
        if not result.pruned:
            continue
        reaped += 1
        if result.salvaged:
            salvaged += 1
        if result.branch_retained:
            branches_retained += 1
    ghost_rows = _surface_registry_ghost_rows(rows=rows, active=active)
    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
        active=active,
    )
    stale_metadata_pruned = _git_worktree_prune(source_repo=source_repo)
    branches_gc = gc_merged_dispatch_branches(source_repo=source_repo)
    debts_reconciled = _reconcile_orphaned_debts(source_repo=source_repo)
    debts_escalated = _escalate_aged_debts()
    return ReapSweepResult(
        reaped=reaped,
        salvaged=salvaged,
        branches_retained=branches_retained,
        branches_gc=branches_gc,
        stale_metadata_pruned=stale_metadata_pruned,
        salvage_refused=salvage_refused,
        debts_escalated=debts_escalated,
        debts_reconciled=debts_reconciled,
        worktrees_reconciled=reconciled,
        worktrees_surfaced=surfaced,
        registry_ghost_rows=ghost_rows,
        live_bridge_holds=len(held),
        vanished_pins=vanished,
    )


def _surface_registry_ghost_rows(
    *,
    rows: list[sqlite3.Row],
    active: set[str],
) -> int:
    """Count lane registry rows whose worktree directory is gone."""
    global _ghost_rows_emitted
    ghosts = 0
    suppressed = 0
    for row in rows:
        path = str(Path(row["worktree_path"]).resolve())
        if path in active or Path(path).is_dir():
            continue
        ghosts += 1
        if path in _ghost_rows_reported:
            continue
        _ghost_rows_reported.add(path)
        if _ghost_rows_emitted >= _GHOST_EMIT_BUDGET:
            suppressed += 1
            continue
        _ghost_rows_emitted += 1
        logger.warning(
            "lane_b registry row outlived its worktree thread_id=%s path=%s branch=%s",
            row["thread_id"],
            path,
            row["branch_name"],
        )
        emit_sdk_lane_b_registry_ghost_row(
            worktree_path=path,
            thread_id=str(row["thread_id"] or "") or None,
            branch=row["branch_name"],
            dispatch_id=row["dispatch_id"],
        )
    if suppressed:
        logger.warning(
            "lane_b registry ghost rows beyond emit budget ghosts=%d suppressed=%d "
            "budget=%d",
            ghosts,
            suppressed,
            _GHOST_EMIT_BUDGET,
        )
    return ghosts


def _reconcile_orphaned_debts(*, source_repo: Path) -> int:
    from services.git_integration_worker.cursor_sdk_branch_debt_reconcile import (
        reconcile_open_branch_debts,
    )

    try:
        report = reconcile_open_branch_debts(source_repo=source_repo, apply=True)
    except Exception as exc:
        logger.warning("branch debt reconcile sweep failed: %s", exc)
        return 0
    return sum(1 for row in report.verdicts if row.applied)


def _escalate_aged_debts() -> int:
    from services.git_integration_worker.cursor_sdk_branch_debt_escalation import (
        escalate_aged_debts,
    )

    try:
        return escalate_aged_debts()
    except Exception as exc:
        logger.warning("branch debt escalation sweep failed: %s", exc)
        return 0
