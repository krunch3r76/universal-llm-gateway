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
    emit_sdk_lane_b_salvage_failed,
    emit_sdk_lane_b_salvaged,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    branch_state,
    is_worktree_dirty,
    salvage_commit,
)
from services.git_integration_worker.cursor_sdk_worktree_gc import (
    _delete_orphan_branch,
    gc_merged_dispatch_branches,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
    live_bridge_worktree_paths,
    live_ledger_worktree_paths,
    worktree_held_by_live_bridge,
)
from services.git_integration_worker.cursor_sdk_worktree_reconcile import (
    reconcile_unregistered_worktrees,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    list_registered_worktrees_with_status,
    lookup_dispatch_worktree,
    unregister_dispatch_worktree,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
_REAPABLE_STATUSES = frozenset({"completed", "failed", "cancelled"})
# Ghost rows are surfaced once per process, not once per 30s sweep: the drift is
# persistent by nature and an every-cycle event would bury the live-bridge skip.
_ghost_rows_reported: set[str] = set()


def reset_ghost_row_reports() -> None:
    """Clear process-local ghost-row report memory (tests only)."""
    _ghost_rows_reported.clear()


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


def is_reapable_dispatch_status(status: str | None) -> bool:
    """True when a dispatch row is an explicit terminal token.

    ``None`` means no live writer on a standing lane — idle, not orphan.
    The registry status subquery only projects admitted/running, so a
    registered row with NULL status must not enter the reap loop.
    """
    return status in _REAPABLE_STATUSES


def prune_dispatch_worktree(
    *,
    dispatch_id: str,
    source_repo: Path,
) -> PruneResult:
    """Remove a registered dispatch worktree; retain unmerged branches (S3).

    Fails closed: when the worktree holds work that git refused to commit, the
    worktree is the only copy, so it is kept and the registry row is left intact.
    """
    from services.git_integration_worker.cursor_sdk_resume import dispatch_retain_active

    if dispatch_retain_active(dispatch_id=dispatch_id):
        return PruneResult(pruned=False)
    record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    if record is None:
        return PruneResult(pruned=False)
    wt_path = record.worktree_path
    branch = record.branch_name
    branch_point = record.branch_point
    repo = source_repo.resolve()
    # Process truth outranks every record we hold: a bridge standing in this
    # directory loses its shell the moment we remove it (spawn ENOENT, H4).
    holder_pid = worktree_held_by_live_bridge(worktree_path=wt_path)
    if holder_pid is not None:
        logger.warning(
            "lane_b prune skipped — live bridge holds worktree dispatch_id=%s "
            "path=%s pid=%s",
            dispatch_id,
            wt_path,
            holder_pid,
        )
        emit_sdk_lane_b_reap_skipped_live_bridge(
            worktree_path=str(wt_path),
            pid=holder_pid,
            dispatch_id=dispatch_id,
            stage="prune",
        )
        return PruneResult(pruned=False, branch_retained=True)
    salvaged = False
    salvage = None
    if wt_path.is_dir() and is_worktree_dirty(wt_path):
        salvage = salvage_commit(
            wt_path,
            message=f"cursor-sdk: prune salvage {dispatch_id}",
        )
        salvaged = salvage.committed
    if salvage is not None and salvage.refused:
        logger.error(
            "lane_b prune aborted — unsalvaged work retained dispatch_id=%s "
            "path=%s branch=%s err=%s",
            dispatch_id,
            wt_path,
            branch,
            salvage.error,
        )
        emit_sdk_lane_b_salvage_failed(
            dispatch_id=dispatch_id,
            branch=branch,
            worktree_path=str(wt_path),
            error=salvage.error,
        )
        return PruneResult(
            pruned=False,
            branch_retained=True,
            salvaged=False,
            head_sha=salvage.head_sha,
            salvage_refused=True,
        )
    # Empty branch + uncommitted work is the only copy — never remove the worktree.
    state_pre = branch_state(
        repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    if (
        wt_path.is_dir()
        and is_worktree_dirty(wt_path)
        and (state_pre.commits_ahead is None or state_pre.commits_ahead == 0)
        and (salvage is None or not salvage.committed)
    ):
        logger.error(
            "lane_b prune aborted — dirty worktree on empty branch retained "
            "dispatch_id=%s path=%s branch=%s",
            dispatch_id,
            wt_path,
            branch,
        )
        emit_sdk_lane_b_salvage_failed(
            dispatch_id=dispatch_id,
            branch=branch,
            worktree_path=str(wt_path),
            error="uncommitted work on empty branch",
        )
        return PruneResult(
            pruned=False,
            branch_retained=True,
            salvaged=False,
            head_sha=salvage.head_sha if salvage is not None else state_pre.head_sha,
            salvage_refused=True,
        )
    if wt_path.is_dir():
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "worktree",
                "remove",
                "--force",
                str(wt_path),
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "worktree remove failed dispatch_id=%s path=%s err=%s",
                dispatch_id,
                wt_path,
                proc.stderr.strip(),
            )
    state = branch_state(
        repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    branch_retained = not state.safe_to_delete
    if salvaged and salvage is not None and salvage.head_sha:
        emit_sdk_lane_b_salvaged(
            dispatch_id=dispatch_id,
            thread_id=dispatch_id,
            head_sha=salvage.head_sha,
            trigger="reaper",
        )
    branch_deleted = False
    if state.safe_to_delete:
        branch_deleted = _delete_orphan_branch(
            repo=repo,
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
    unregister_dispatch_worktree(dispatch_id=dispatch_id)
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
        salvaged=salvaged,
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
    """Worktree paths under ``worktree_root`` that no sweep may remove.

    Three sources, unioned, because any one of them can be wrong on its own:
    the ledger lease key (blind when the key points outside the root), the
    lane registry joined to non-terminal rows (blind when registry status
    lags), and live bridge processes (blind to nothing, but only sees what is
    running this instant).
    """
    root = str(worktree_root.resolve())
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
        if key.startswith(root):
            active.add(str(Path(key).resolve()))
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
            dispatch_id=row["dispatch_id"] or row["thread_id"],
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
    # Before escalation: a debt this resolves is discharged, so the aged sweep
    # does not announce residue that no longer needs an owner's attention.
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
    )


def _surface_registry_ghost_rows(
    *,
    rows: list[sqlite3.Row],
    active: set[str],
) -> int:
    """Count lane registry rows whose worktree directory is gone; report each once.

    The row is left in place. It still pins its branch against merged-branch
    GC, and the tree it named is exactly the case where the tip may be the only
    copy of that work — so the coherent action is to make the drift visible,
    not to drop the record. Rows the guard reports as active are skipped: a
    lane whose directory is mid-mint is not a ghost.
    """
    ghosts = 0
    for row in rows:
        path = str(Path(row["worktree_path"]).resolve())
        if path in active or Path(path).is_dir():
            continue
        ghosts += 1
        if path in _ghost_rows_reported:
            continue
        _ghost_rows_reported.add(path)
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
    return ghosts


def _reconcile_orphaned_debts(*, source_repo: Path) -> int:
    """Retire debts whose branch ref is gone; never fatal to the sweep."""
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
    """Raise aged debt on its owning thread; never fatal to the sweep."""
    from services.git_integration_worker.cursor_sdk_branch_debt_escalation import (
        escalate_aged_debts,
    )

    try:
        return escalate_aged_debts()
    except Exception as exc:
        logger.warning("branch debt escalation sweep failed: %s", exc)
        return 0
