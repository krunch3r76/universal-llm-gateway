"""Lane-B worktree prune-on-terminal and orphan reaper (S1b/S3/S6)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import _connect
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_branch_retained,
    emit_sdk_lane_b_reaped,
    emit_sdk_lane_b_salvage_failed,
    emit_sdk_lane_b_salvaged,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    branch_state,
    is_worktree_dirty,
    salvage_commit,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    list_registered_worktrees_with_status,
    lookup_dispatch_worktree,
    unregister_dispatch_worktree,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
_DISPATCH_BRANCH_PREFIX = "cursor-sdk/"
_REAPABLE_STATUSES = frozenset({"completed", "failed", "cancelled"})


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


def is_reapable_dispatch_status(status: str | None) -> bool:
    """True when a dispatch row is terminal enough for orphan worktree reaping."""
    return status is None or status in _REAPABLE_STATUSES


def prune_dispatch_worktree(
    *,
    dispatch_id: str,
    source_repo: Path,
) -> PruneResult:
    """Remove a registered dispatch worktree; retain unmerged branches (S3).

    Fails closed: when the worktree holds work that git refused to commit, the
    worktree is the only copy, so it is kept and the registry row is left intact.
    """
    record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    if record is None:
        return PruneResult(pruned=False)
    wt_path = record.worktree_path
    branch = record.branch_name
    branch_point = record.branch_point
    repo = source_repo.resolve()
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
        and state_pre.commits_ahead == 0
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
    if branch_retained:
        emit_sdk_lane_b_branch_retained(
            dispatch_id=dispatch_id,
            branch=branch,
            commits_ahead=state.commits_ahead,
        )
    if state.safe_to_delete:
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", branch],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    unregister_dispatch_worktree(dispatch_id=dispatch_id)
    emit_sdk_lane_b_reaped(
        dispatch_id=dispatch_id,
        branch_deleted=state.safe_to_delete,
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
    """Prune-on-terminal for minted Lane-B worktrees."""
    return prune_dispatch_worktree(dispatch_id=dispatch_id, source_repo=source_repo)


def active_managed_worktree_paths(*, worktree_root: Path) -> set[str]:
    """Resolved worktree paths for non-terminal dispatches under ``worktree_root``."""
    root = str(worktree_root.resolve())
    active: set[str] = set()
    with _connect() as conn:
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


def _registered_branch_names() -> set[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT branch_name FROM cursor_sdk_dispatch_worktrees"
        ).fetchall()
    return {row["branch_name"] for row in rows}


def gc_merged_dispatch_branches(*, source_repo: Path) -> int:
    """Delete merged ``cursor-sdk/*`` branches with no live registry row (S6)."""
    repo = source_repo.resolve()
    registered = _registered_branch_names()
    merged_proc = subprocess.run(
        ["git", "-C", str(repo), "branch", "--merged", "master"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if merged_proc.returncode != 0:
        return 0
    deleted = 0
    for line in merged_proc.stdout.splitlines():
        name = line.strip().lstrip("* ").strip()
        if not name.startswith(_DISPATCH_BRANCH_PREFIX):
            continue
        if name in registered:
            continue
        del_proc = subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", name],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        if del_proc.returncode == 0:
            deleted += 1
    return deleted


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
    rows = list_registered_worktrees_with_status()
    for row in rows:
        wt_path = str(Path(row["worktree_path"]).resolve())
        status = row["status"]
        if wt_path in active:
            continue
        if not is_reapable_dispatch_status(status):
            continue
        result = prune_dispatch_worktree(
            dispatch_id=row["dispatch_id"],
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
    stale_metadata_pruned = _git_worktree_prune(source_repo=source_repo)
    branches_gc = gc_merged_dispatch_branches(source_repo=source_repo)
    return ReapSweepResult(
        reaped=reaped,
        salvaged=salvaged,
        branches_retained=branches_retained,
        branches_gc=branches_gc,
        stale_metadata_pruned=stale_metadata_pruned,
        salvage_refused=salvage_refused,
    )
