"""land_op: commit-aware atomic arc-worktree integration."""

from __future__ import annotations

import asyncio
import time
import uuid

from universal_logging import get_logger

from git_integrate.events import (
    emit_git_commit_created,
    emit_git_integrate_rejected,
    emit_git_land_completed,
    emit_git_land_requested,
)
from git_integrate.git_cas import commit_arc, diff_sha256, is_dirty
from git_integrate.ops_common import (
    envelope,
    integrate_retry_loop,
    worktree_remove_clean,
)
from git_integrate.schema import (
    RC_COMMIT_FAILED,
    RC_DIFF_MISMATCH,
    RC_TEARDOWN_FAILED,
)
from git_integrate.validate import validate_integrate, validate_land

_logger = get_logger(__name__)

# Land-report disambiguation (thread 1153): a land advances the *ref*
# refs/heads/master in source_repo — the authoritative land target. The live
# working checkout ff-pulls on its own cadence and origin push is
# operator-discretionary; neither is implied by a completed land. Reconcile
# "landed" claims against the ref (via git_cas.is_reachable_from_master /
# GET /api/v1/git/reachable), never a working tree's HEAD.
_LAND_REPORT_NOTE = (
    "master_sha is the advanced tip of refs/heads/master in source_repo (the "
    "authoritative land target). The live working checkout ff-pulls on its own "
    "cadence and origin push is operator-discretionary — neither is implied by "
    "this land. Reconcile reachability against the ref, not a working tree."
)


async def land_op(
    *,
    arc: str,
    phase: str,
    worktree_path: str,
    approval: str,
    expected_diff_sha256: str,
    source_repo: str,
    green_gate_cmd: list[str],
    commit_message: str = "",
    remove_worktree: bool = True,
    max_attempts: int = 5,
) -> dict:
    """Atomic land: commit arc (if dirty), merge, gate, CAS-advance, teardown."""
    integration_id = str(uuid.uuid4())
    t0 = time.monotonic()
    loop = asyncio.get_running_loop()

    dirty = await loop.run_in_executor(None, lambda: is_dirty(worktree_path))

    vr = await loop.run_in_executor(
        None,
        lambda: validate_land(
            arc=arc,
            worktree_path=worktree_path,
            approval=approval,
            expected_diff_sha256=expected_diff_sha256,
            commit_message=commit_message,
            dirty=dirty,
        ),
    )
    if not vr.ok:
        emit_git_integrate_rejected(
            integration_id=integration_id,
            reason_code=vr.reason_code,
            reason=vr.reason,
            arc=arc,
            phase=phase,
        )
        return envelope(
            integration_id=integration_id,
            status="rejected",
            reason_code=vr.reason_code,
            reason=vr.reason,
        )

    committed = False
    commit_sha = ""
    if dirty:
        cr = await commit_arc(worktree_path, commit_message)
        if cr.reason_code == RC_COMMIT_FAILED:
            emit_git_integrate_rejected(
                integration_id=integration_id,
                reason_code=RC_COMMIT_FAILED,
                reason="git commit failed",
                arc=arc,
                phase=phase,
            )
            return envelope(
                integration_id=integration_id,
                status="rejected",
                reason_code=RC_COMMIT_FAILED,
                reason="git commit failed",
            )
        if cr.committed:
            committed = True
            commit_sha = cr.commit_sha
            emit_git_commit_created(
                integration_id=integration_id,
                arc=arc,
                commit_sha=commit_sha,
            )

        post_commit = await loop.run_in_executor(
            None,
            lambda: validate_integrate(
                arc=arc,
                worktree_path=worktree_path,
                approval=approval,
                expected_diff_sha256=expected_diff_sha256,
            ),
        )
        if not post_commit.ok:
            emit_git_integrate_rejected(
                integration_id=integration_id,
                reason_code=post_commit.reason_code,
                reason=post_commit.reason,
                arc=arc,
                phase=phase,
            )
            return envelope(
                integration_id=integration_id,
                status="rejected",
                reason_code=post_commit.reason_code,
                reason=post_commit.reason,
            )
        actual = diff_sha256(worktree_path)
        if actual != expected_diff_sha256:
            emit_git_integrate_rejected(
                integration_id=integration_id,
                reason_code=RC_DIFF_MISMATCH,
                reason="post-commit fingerprint mismatch",
                arc=arc,
                phase=phase,
            )
            return envelope(
                integration_id=integration_id,
                status="rejected",
                reason_code=RC_DIFF_MISMATCH,
                reason="post-commit fingerprint mismatch",
            )

    emit_git_land_requested(
        integration_id=integration_id,
        arc=arc,
        phase=phase,
        worktree_path=worktree_path,
        diff_sha256=expected_diff_sha256,
        committed=committed,
    )

    loop_result = await integrate_retry_loop(
        integration_id=integration_id,
        arc=arc,
        phase=phase,
        worktree_path=worktree_path,
        source_repo=source_repo,
        green_gate_cmd=green_gate_cmd,
        max_attempts=max_attempts,
        t0=t0,
    )
    if loop_result.get("status") == "rejected":
        return loop_result

    master_sha = loop_result["master_sha"]
    merge_commit = loop_result["merge_commit"]

    teardown_warning = ""
    if remove_worktree:
        err = await worktree_remove_clean(worktree_path)
        if err:
            teardown_warning = err
            _logger.warning(
                "worktree teardown failed after successful land",
                extra={"worktree_path": worktree_path, "error": err},
            )

    duration_s = time.monotonic() - t0

    emit_git_land_completed(
        integration_id=integration_id,
        arc=arc,
        phase=phase,
        merge_commit=merge_commit,
        master_sha=master_sha,
        committed=committed,
        commit_sha=commit_sha,
        duration_s=duration_s,
    )

    result = envelope(
        integration_id=integration_id,
        status="completed",
        merge_commit=merge_commit,
        master_sha=master_sha,
        landed_ref="refs/heads/master",
        land_report=_LAND_REPORT_NOTE,
        committed=committed,
        commit_sha=commit_sha,
        duration_s=duration_s,
    )
    if teardown_warning:
        result["teardown_warning"] = teardown_warning
        result["reason_code"] = RC_TEARDOWN_FAILED
    return result
