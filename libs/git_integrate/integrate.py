"""integrate_op: Template-Method atomic arc-worktree integration executor."""

from __future__ import annotations

import asyncio
import time
import uuid

from universal_logging import get_logger

from git_integrate.events import (
    emit_git_integrate_completed,
    emit_git_integrate_rejected,
    emit_git_integrate_requested,
)
from git_integrate.ops_common import (
    envelope,
    integrate_retry_loop,
    worktree_remove_clean,
)
from git_integrate.schema import RC_TEARDOWN_FAILED
from git_integrate.validate import validate_integrate

_logger = get_logger(__name__)


async def integrate_op(
    *,
    arc: str,
    phase: str,
    worktree_path: str,
    approval: str,
    expected_diff_sha256: str,
    source_repo: str,
    green_gate_cmd: list[str],
    remove_worktree: bool = True,
    max_attempts: int = 5,
) -> dict:
    """Atomic arc-worktree integration: merge master, gate, CAS-advance master.

    green_gate_cmd is SERVER-CONFIGURED (OQ1) — injected by the worker,
    never supplied by the agent calling this function.
    """
    integration_id = str(uuid.uuid4())
    t0 = time.monotonic()
    loop = asyncio.get_running_loop()

    vr = await loop.run_in_executor(
        None,
        lambda: validate_integrate(
            arc=arc,
            worktree_path=worktree_path,
            approval=approval,
            expected_diff_sha256=expected_diff_sha256,
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

    emit_git_integrate_requested(
        integration_id=integration_id,
        arc=arc,
        phase=phase,
        worktree_path=worktree_path,
        diff_sha256=expected_diff_sha256,
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
                "worktree teardown failed after successful integration",
                extra={"worktree_path": worktree_path, "error": err},
            )

    duration_s = time.monotonic() - t0

    emit_git_integrate_completed(
        integration_id=integration_id,
        arc=arc,
        phase=phase,
        merge_commit=merge_commit,
        master_sha=master_sha,
        duration_s=duration_s,
    )

    result = envelope(
        integration_id=integration_id,
        status="completed",
        merge_commit=merge_commit,
        master_sha=master_sha,
        duration_s=duration_s,
    )
    if teardown_warning:
        result["teardown_warning"] = teardown_warning
        result["reason_code"] = RC_TEARDOWN_FAILED
    return result
