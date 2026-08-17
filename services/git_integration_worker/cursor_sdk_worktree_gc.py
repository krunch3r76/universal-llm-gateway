"""Lane-B branch GC — delete unused ``cursor-sdk/*`` residue only.

Mechanical safety (empty / merged / content-landed) is necessary, not
sufficient. Standing lanes, checked-out heads, and non-sdk prefixes stay.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import _connect
from services.git_integration_worker.cursor_sdk_events import emit_sdk_lane_b_reaped

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
DISPATCH_BRANCH_PREFIX = "cursor-sdk/"
PROTECTED_BRANCHES = frozenset({"master", "main", "HEAD"})


def is_cursor_sdk_branch(name: str) -> bool:
    """True when *name* is a Lane-B managed branch."""
    return name.startswith(DISPATCH_BRANCH_PREFIX)


def is_lane_b_reconcile_target(*, branch: str | None, path: Path) -> bool:
    """True when an unregistered tree is Lane-B residue, not an arc/wip tree."""
    if branch and is_cursor_sdk_branch(branch):
        return True
    return branch is None and path.name.startswith("lane-")


def registered_branch_names() -> set[str]:
    with _connect() as conn:
        from services.git_integration_worker.cursor_sdk_worktree_registry import (
            ensure_worktree_schema,
        )

        ensure_worktree_schema(conn)
        rows = conn.execute(
            "SELECT branch_name FROM cursor_sdk_lane_worktrees"
        ).fetchall()
    return {row["branch_name"] for row in rows}


def gc_merged_dispatch_branches(*, source_repo: Path) -> int:
    """Delete unused landed ``cursor-sdk/*`` orphans; archive every tip first.

    Pass 1 is prefix-scoped: ``git branch --merged master`` is not a license
    to vacuum ``arc/*`` or other local heads. Pass 2 stays lane-scoped
    (disposition / content-landed) and inherits the same delete gates.
    """
    from services.git_integration_worker.cursor_sdk_lane_b_commit import (
        list_cursor_sdk_branches,
        normalize_git_branch_list_name,
        orphan_branch_state,
    )
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        clear_disposition,
        get_disposition,
    )

    repo = source_repo.resolve()
    registered = registered_branch_names()
    deleted = 0

    merged_proc = subprocess.run(
        ["git", "-C", str(repo), "branch", "--merged", "master"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if merged_proc.returncode == 0:
        for line in merged_proc.stdout.splitlines():
            name = normalize_git_branch_list_name(line)
            if not name or name in PROTECTED_BRANCHES:
                continue
            if not is_cursor_sdk_branch(name):
                continue
            if name in registered:
                continue
            if _delete_orphan_branch(
                repo=repo,
                branch_name=name,
                reason="ancestry_merged",
                dispatch_id=None,
            ):
                clear_disposition(branch_name=name)
                deleted += 1

    for name in list_cursor_sdk_branches(repo):
        if name in registered:
            continue
        disposition = get_disposition(branch_name=name)
        if disposition is not None:
            if _delete_orphan_branch(
                repo=repo,
                branch_name=name,
                reason=disposition.reason,
                dispatch_id=disposition.dispatch_id,
                tip_sha=disposition.tip_sha,
            ):
                clear_disposition(branch_name=name)
                deleted += 1
            continue
        state = orphan_branch_state(repo, branch_name=name)
        if state.safe_to_delete:
            reason = "content_landed" if state.content_landed else "mechanical_safe"
            if _delete_orphan_branch(
                repo=repo,
                branch_name=name,
                reason=reason,
                dispatch_id=None,
                tip_sha=state.head_sha,
            ):
                deleted += 1
    return deleted


def _delete_orphan_branch(
    *,
    repo: Path,
    branch_name: str,
    reason: str,
    dispatch_id: str | None,
    tip_sha: str | None = None,
) -> bool:
    """Archive then delete *branch_name*, emitting ``sdk.lane_b.reaped``.

    Archiving is a precondition, not a courtesy: a tip we could not preserve is
    a tip we do not delete, whatever the safety proof said. Checked-out and
    open-debt heads are in use — refuse before archive.
    """
    from services.git_integration_worker.cursor_sdk_branch_archive import (
        archive_branch,
        branch_checked_out_at,
    )
    from services.git_integration_worker.cursor_sdk_branch_debt import (
        discharge_branch_debt,
        get_branch_debt,
    )

    pinned = branch_checked_out_at(repo=repo, branch_name=branch_name)
    if pinned is not None:
        logger.info(
            "orphan branch delete skipped — checked out branch=%s path=%s",
            branch_name,
            pinned,
        )
        return False

    debt = get_branch_debt(branch_name=branch_name)
    if debt is not None and debt.open:
        logger.info(
            "orphan branch delete skipped — open debt branch=%s dispatch_id=%s",
            branch_name,
            debt.dispatch_id,
        )
        return False

    if archive_branch(repo=repo, branch_name=branch_name) is None:
        logger.warning(
            "orphan branch delete skipped — archive failed branch=%s", branch_name
        )
        return False
    del_proc = subprocess.run(
        ["git", "-C", str(repo), "branch", "-D", branch_name],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if del_proc.returncode != 0:
        return False
    discharge_branch_debt(branch_name=branch_name, verb="landed", note=reason)
    emit_sdk_lane_b_reaped(
        dispatch_id=dispatch_id or "",
        branch_deleted=True,
        branch=branch_name,
        tip_sha=tip_sha,
        reason=reason,
    )
    return True
