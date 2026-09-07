"""Unpin a registered Lane-B worktree so discharge can delete the branch."""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_branch_archive import (
    branch_checked_out_at,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_reap_skipped_live_bridge,
    emit_sdk_lane_b_worktree_removed,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import salvage_commit
from services.git_integration_worker.cursor_sdk_lane_inherit import thread_has_inheritor
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
    worktree_held_by_live_bridge,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    _ledger_status_for_dispatch,
)
from services.git_integration_worker.cursor_sdk_worktree_reconcile import (
    list_git_worktrees,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    DispatchWorktreeRecord,
    ensure_worktree_schema,
    unregister_lane_worktree,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class UnpinResult:
    """Whether the registered checkout was removed, inherited, or refused."""

    unpinned: bool
    inherited: bool = False
    refused_reason: str | None = None


def _record_for_branch(
    *,
    source_repo: Path,
    branch_name: str,
) -> DispatchWorktreeRecord | None:
    try:
        with ledger_connection() as conn:
            ensure_worktree_schema(conn)
            row = conn.execute(
                "SELECT source_repo, thread_id, worktree_path, branch_name, "
                "branch_point, last_dispatch_id FROM cursor_sdk_lane_worktrees "
                "WHERE source_repo=? AND branch_name=?",
                (str(source_repo.resolve()), branch_name),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return DispatchWorktreeRecord(
        worktree_path=Path(row["worktree_path"]),
        branch_name=row["branch_name"],
        branch_point=row["branch_point"],
        thread_id=str(row["thread_id"] or ""),
        last_dispatch_id=row["last_dispatch_id"],
        source_repo=str(row["source_repo"] or ""),
    )


def _is_git_worktree(*, repo: Path, worktree_path: Path) -> bool:
    target = worktree_path.resolve()
    return any(wt.path.resolve() == target for wt in list_git_worktrees(source_repo=repo))


def _remove_worktree(*, repo: Path, worktree_path: Path) -> str | None:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "remove",
            "--force",
            str(worktree_path),
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode == 0:
        return None
    return proc.stderr.strip() or "git worktree remove failed"


def unpin_registered_lane_worktree(
    *,
    repo: Path,
    branch_name: str,
    completing_dispatch_id: str | None = None,
) -> UnpinResult:
    """Remove the registry row's worktree so ``git branch -D`` can proceed.

    Unregistered checkouts stay refused. A successor on the same thread
    inherits — the tree is left in place. Dirty trees are salvaged onto the
    branch before remove; a salvage refusal keeps the only copy.
    """
    root = repo.resolve()
    record = _record_for_branch(source_repo=root, branch_name=branch_name)
    pinned = branch_checked_out_at(repo=root, branch_name=branch_name)
    if record is None:
        if pinned is not None:
            return UnpinResult(
                unpinned=False,
                refused_reason=f"branch checked out at {pinned}",
            )
        return UnpinResult(unpinned=True)

    if record.thread_id and thread_has_inheritor(
        record.thread_id,
        completing_dispatch_id=completing_dispatch_id,
    ):
        logger.info(
            "lane_b unpin skipped inherit branch=%s thread=%s",
            branch_name,
            record.thread_id,
        )
        return UnpinResult(
            unpinned=False,
            inherited=True,
            refused_reason="inherited by successor",
        )

    registered = record.worktree_path.resolve()

    if registered.is_dir() and _is_git_worktree(repo=root, worktree_path=registered):
        holder_pid = worktree_held_by_live_bridge(
            worktree_path=registered,
            fresh=True,
        )
        if holder_pid is not None:
            logger.warning(
                "lane_b unpin skipped — live bridge holds worktree branch=%s "
                "path=%s pid=%s",
                branch_name,
                registered,
                holder_pid,
            )
            emit_sdk_lane_b_reap_skipped_live_bridge(
                worktree_path=str(registered),
                pid=holder_pid,
                dispatch_id=record.last_dispatch_id,
                stage="unpin",
            )
            return UnpinResult(
                unpinned=False,
                refused_reason="live bridge holds worktree",
            )
        salvage = salvage_commit(
            registered,
            message=f"cursor-sdk: discharge salvage {branch_name}",
        )
        if salvage.refused:
            return UnpinResult(
                unpinned=False,
                refused_reason=f"salvage refused: {salvage.error}",
            )
        error = _remove_worktree(repo=root, worktree_path=registered)
        if error is not None:
            return UnpinResult(unpinned=False, refused_reason=error)
        ledger_status = "none"
        if record.last_dispatch_id:
            ledger_status = _ledger_status_for_dispatch(
                dispatch_id=record.last_dispatch_id
            )
        emit_sdk_lane_b_worktree_removed(
            worktree_path=str(registered),
            trigger="unpin",
            ledger_status_at_remove=ledger_status,
            dispatch_id=record.last_dispatch_id,
            thread_id=record.thread_id or None,
            branch=branch_name,
        )

    if record.thread_id:
        unregister_lane_worktree(
            thread_id=record.thread_id,
            source_repo=root,
        )
    logger.info(
        "lane_b worktree unpinned branch=%s path=%s",
        branch_name,
        registered,
    )
    return UnpinResult(unpinned=True)


__all__ = ["UnpinResult", "unpin_registered_lane_worktree"]
