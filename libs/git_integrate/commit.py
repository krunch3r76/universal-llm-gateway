"""commit_op: gated, path-scoped commit on a non-arc working tree.

Plain commit on the current branch — no merge, no CAS, no teardown. Commits
ONLY the explicitly named paths, operator-approval-gated and bound to a
path-scoped fingerprint. ``dry_run`` returns the fingerprint + numstat for
approval binding without committing (read-only).
"""

from __future__ import annotations

import asyncio
import time
import uuid

from universal_logging import get_logger

from git_integrate.commit_paths import (
    commit_paths,
    commit_paths_fingerprint,
    commit_paths_numstat,
)
from git_integrate.events import (
    emit_git_path_commit_completed,
    emit_git_path_commit_rejected,
)
from git_integrate.ops_common import envelope
from git_integrate.schema import RC_COMMIT_FAILED
from git_integrate.validate import validate_commit

_logger = get_logger(__name__)


async def commit_op(
    *,
    worktree_path: str,
    expected_branch: str,
    paths: list[str],
    approval: str = "",
    expected_paths_sha256: str = "",
    commit_message: str = "",
    dry_run: bool = False,
) -> dict:
    """Gated path-scoped commit, or read-only preview when ``dry_run`` is set."""
    commit_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()

    if dry_run:
        fingerprint = await loop.run_in_executor(
            None, lambda: commit_paths_fingerprint(worktree_path, paths)
        )
        numstat = await loop.run_in_executor(
            None, lambda: commit_paths_numstat(worktree_path, paths)
        )
        return envelope(
            integration_id=commit_id,
            status="preview",
            expected_paths_sha256=fingerprint,
            numstat=numstat,
            branch=expected_branch,
        )

    t0 = time.monotonic()
    vr = await loop.run_in_executor(
        None,
        lambda: validate_commit(
            worktree_path=worktree_path,
            expected_branch=expected_branch,
            paths=paths,
            approval=approval,
            expected_paths_sha256=expected_paths_sha256,
            commit_message=commit_message,
        ),
    )
    if not vr.ok:
        emit_git_path_commit_rejected(
            commit_id=commit_id,
            reason_code=vr.reason_code,
            reason=vr.reason,
            branch=expected_branch,
        )
        return envelope(
            integration_id=commit_id,
            status="rejected",
            reason_code=vr.reason_code,
            reason=vr.reason,
        )

    cr = await commit_paths(worktree_path, paths, commit_message)
    if not cr.committed:
        reason_code = cr.reason_code or RC_COMMIT_FAILED
        emit_git_path_commit_rejected(
            commit_id=commit_id,
            reason_code=reason_code,
            reason="commit failed",
            branch=expected_branch,
        )
        return envelope(
            integration_id=commit_id,
            status="rejected",
            reason_code=reason_code,
            reason="commit failed",
        )

    duration_s = time.monotonic() - t0
    emit_git_path_commit_completed(
        commit_id=commit_id,
        branch=expected_branch,
        commit_sha=cr.commit_sha,
        path_count=len(paths),
        duration_s=duration_s,
    )
    return envelope(
        integration_id=commit_id,
        status="completed",
        committed=True,
        commit_sha=cr.commit_sha,
        branch=expected_branch,
        duration_s=duration_s,
    )
