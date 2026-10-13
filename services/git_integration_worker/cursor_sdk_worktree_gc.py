"""Lane-B branch GC — delete unused ``cursor-sdk/*`` residue only.

Mechanical safety (empty / merged / content-landed) is necessary, not
sufficient. Standing lanes, checked-out heads, and non-sdk prefixes stay.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_sdk_lane_b_reaped
from services.git_integration_worker.cursor_sdk_reap_pytest_guard import (
    log_reap_pass,
    refuse_real_hub_reap_under_pytest,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
DISPATCH_BRANCH_PREFIX = "cursor-sdk/"
PROTECTED_BRANCHES = frozenset({"master", "main", "HEAD"})

OrphanBranchDeleteResult = Literal["deleted", "refused", "failed"]


def is_cursor_sdk_branch(name: str) -> bool:
    """True when *name* is a Lane-B managed branch."""
    return name.startswith(DISPATCH_BRANCH_PREFIX)


def is_lane_b_reconcile_target(*, branch: str | None, path: Path) -> bool:
    """True when an unregistered tree is Lane-B residue, not an arc/wip tree."""
    if branch and is_cursor_sdk_branch(branch):
        return True
    return branch is None and path.name.startswith("lane-")


def registered_branch_names() -> set[str]:
    with ledger_connection() as conn:
        from services.git_integration_worker.cursor_sdk_worktree_registry import (
            ensure_worktree_schema,
        )

        ensure_worktree_schema(conn)
        rows = conn.execute(
            "SELECT branch_name FROM cursor_sdk_lane_worktrees"
        ).fetchall()
    return {row["branch_name"] for row in rows}


def _gc_delete_candidates(*, source_repo: Path) -> list[tuple[str, str, str | None, str | None]]:
    """Return ``(branch_name, reason, dispatch_id, tip_sha)`` rows eligible for delete."""
    from services.git_integration_worker.cursor_sdk_lane_b_commit import (
        list_cursor_sdk_branches,
        normalize_git_branch_list_name,
        orphan_branch_state,
    )
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        get_disposition,
    )

    repo = source_repo.resolve()
    registered = registered_branch_names()
    candidates: list[tuple[str, str, str | None, str | None]] = []

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
            candidates.append((name, "ancestry_merged", None, None))

    seen = {name for name, *_ in candidates}
    for name in list_cursor_sdk_branches(repo):
        if name in registered or name in seen:
            continue
        disposition = get_disposition(branch_name=name)
        if disposition is not None:
            if disposition.reason == "retained_for_mission":
                continue
            candidates.append(
                (
                    name,
                    disposition.reason,
                    disposition.dispatch_id,
                    disposition.tip_sha,
                )
            )
            continue
        state = orphan_branch_state(repo, branch_name=name)
        if state.safe_to_delete:
            reason = "content_landed" if state.content_landed else "mechanical_safe"
            candidates.append((name, reason, None, state.head_sha))
    return candidates


def gc_merged_dispatch_branches(
    *,
    source_repo: Path,
    delete_candidates: list[tuple[str, str, str | None, str | None]] | None = None,
) -> int:
    """Delete unused landed ``cursor-sdk/*`` orphans; archive every tip first.

    Pass 1 is prefix-scoped: ``git branch --merged master`` is not a license
    to vacuum ``arc/*`` or other local heads. Pass 2 stays lane-scoped
    (disposition / content-landed) and inherits the same delete gates.
    """
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        clear_disposition,
        get_disposition,
    )

    refuse_real_hub_reap_under_pytest(source_repo=source_repo)
    repo = source_repo.resolve()
    candidates = (
        delete_candidates
        if delete_candidates is not None
        else _gc_delete_candidates(source_repo=source_repo)
    )
    log_reap_pass(
        pass_name="gc_merged_dispatch_branches",
        source_repo=repo,
        action_count=len(candidates),
    )
    deleted = 0

    for name, reason, dispatch_id, tip_sha in candidates:
        if not name or name in PROTECTED_BRANCHES:
            continue
        if not is_cursor_sdk_branch(name):
            continue
        disposition = get_disposition(branch_name=name)
        if disposition is not None and disposition.reason == "retained_for_mission":
            continue
        had_disposition = disposition is not None
        if (
            _delete_orphan_branch(
                repo=repo,
                branch_name=name,
                reason=reason,
                dispatch_id=dispatch_id,
                tip_sha=tip_sha,
            )
            == "deleted"
        ):
            if reason == "ancestry_merged" or had_disposition:
                clear_disposition(branch_name=name)
            deleted += 1
    return deleted


def _delete_orphan_branch(
    *,
    repo: Path,
    branch_name: str,
    reason: str,
    dispatch_id: str | None,
    tip_sha: str | None = None,
) -> OrphanBranchDeleteResult:
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

    refuse_real_hub_reap_under_pytest(source_repo=repo)

    pinned = branch_checked_out_at(repo=repo, branch_name=branch_name)
    if pinned is not None:
        logger.info(
            "orphan branch delete skipped — checked out branch=%s path=%s",
            branch_name,
            pinned,
        )
        return "refused"

    debt = get_branch_debt(branch_name=branch_name)
    if debt is not None and debt.open:
        logger.info(
            "orphan branch delete skipped — open debt branch=%s dispatch_id=%s",
            branch_name,
            debt.dispatch_id,
        )
        return "refused"

    if archive_branch(repo=repo, branch_name=branch_name) is None:
        logger.warning(
            "orphan branch delete skipped — archive failed branch=%s", branch_name
        )
        return "refused"

    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        get_disposition,
    )

    disposition = get_disposition(branch_name=branch_name)
    if disposition is not None and disposition.reason == "retained_for_mission":
        logger.info(
            "orphan branch delete skipped — retained_for_mission branch=%s",
            branch_name,
        )
        return "refused"
    log_reap_pass(
        pass_name="_delete_orphan_branch",
        source_repo=repo,
        action_count=1,
    )
    try:
        del_proc = subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", branch_name],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "orphan branch git delete timed out branch=%s timeout_s=%s",
            branch_name,
            _GIT_TIMEOUT_S,
        )
        emit_sdk_lane_b_reaped(
            dispatch_id=dispatch_id or "",
            branch_deleted=False,
            outcome="failed",
            branch=branch_name,
            tip_sha=tip_sha,
            reason=f"git_delete_timeout:{_GIT_TIMEOUT_S}s",
        )
        return "failed"
    if del_proc.returncode != 0:
        stderr = del_proc.stderr.strip()
        logger.warning(
            "orphan branch git delete failed branch=%s err=%s",
            branch_name,
            stderr,
        )
        emit_sdk_lane_b_reaped(
            dispatch_id=dispatch_id or "",
            branch_deleted=False,
            outcome="failed",
            branch=branch_name,
            tip_sha=tip_sha,
            reason=f"git_delete_failed:{stderr}",
        )
        return "failed"
    discharge_branch_debt(branch_name=branch_name, verb="landed", note=reason)
    emit_sdk_lane_b_reaped(
        dispatch_id=dispatch_id or "",
        branch_deleted=True,
        outcome="deleted",
        branch=branch_name,
        tip_sha=tip_sha,
        reason=reason,
    )
    return "deleted"
