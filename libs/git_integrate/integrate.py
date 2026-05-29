"""integrate_op: Template-Method atomic arc-worktree integration executor.

Algorithm (fixed order, lock-free optimistic concurrency):
  1. Admission (validate_integrate — sync, run_in_executor)
  2. Emit requested signal
  3. Optimistic retry loop (fetch, merge, gate, CAS-advance master)
  4. Optional worktree teardown
  5. Emit completed signal + return envelope

The retry loop handles master advancing mid-span (non_ff CAS result) by
re-merging and re-gating on the new master tip. max_attempts caps the loop.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from universal_logging import get_logger

from git_integrate.events import (
    emit_git_integrate_completed,
    emit_git_integrate_gate_failed,
    emit_git_integrate_rejected,
    emit_git_integrate_requested,
    emit_git_integrate_retried,
)
from git_integrate.git_cas import (
    _run_command,
    abort_merge,
    advance_master_cas,
    current_sha,
    fetch_master,
    merge_master_into,
    reset_hard_to,
)
from git_integrate.schema import (
    RC_CAS_EXHAUSTED,
    RC_GATE_FAILED,
    RC_INTEGRATE_CONFLICT,
    RC_TEARDOWN_FAILED,
)
from git_integrate.validate import validate_integrate

_GATE_TIMEOUT = 300.0
_logger = get_logger(__name__)


def _envelope(
    *,
    integration_id: str,
    status: str,
    reason_code: str = "",
    reason: str = "",
    **fields: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "integration_id": integration_id,
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
    }
    result.update(fields)
    return result


async def _worktree_remove_clean(worktree_path: str) -> str:
    """Remove the worktree after successful integration.

    Runs as `git -C <worktree_path> worktree remove <worktree_path>` (same
    pattern as grokbuild.worktree_remove) so git discovers the repo from
    within the worktree itself regardless of process CWD.

    Non-fatal: returns error string on failure so the caller can attach
    teardown_warning to the envelope without downgrading status to failed.
    """
    proc = await _run_command(
        ["git", "-C", worktree_path, "worktree", "remove", worktree_path],
        timeout=30.0,
    )
    if proc.returncode != 0:
        return proc.stderr.strip() or "git worktree remove failed"
    return ""


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
) -> dict[str, Any]:
    """Atomic arc-worktree integration: merge master, gate, CAS-advance master.

    green_gate_cmd is SERVER-CONFIGURED (OQ1) — injected by the worker,
    never supplied by the agent calling this function.
    """
    integration_id = str(uuid.uuid4())
    t0 = time.monotonic()
    loop = asyncio.get_running_loop()

    # 1. ADMISSION — sync checks, run off the event loop
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
        return _envelope(
            integration_id=integration_id,
            status="rejected",
            reason_code=vr.reason_code,
            reason=vr.reason,
        )

    # 2. Signal intent before any destructive git operation
    emit_git_integrate_requested(
        integration_id=integration_id,
        arc=arc,
        phase=phase,
        worktree_path=worktree_path,
        diff_sha256=expected_diff_sha256,
    )

    # 3. OPTIMISTIC RETRY LOOP (lock-free; no mutex per decision:arc-worktree-binding)
    master_sha = ""
    merge_commit = ""
    for attempt in range(1, max_attempts + 1):
        master_before = await current_sha(source_repo, "refs/heads/master")
        await fetch_master(worktree_path)
        arc_tip_before = await current_sha(worktree_path, "HEAD")

        merged = await merge_master_into(worktree_path)
        if merged.conflict:
            await abort_merge(worktree_path)
            emit_git_integrate_rejected(
                integration_id=integration_id,
                reason_code=RC_INTEGRATE_CONFLICT,
                reason="merge conflict between arc branch and master",
                arc=arc,
                phase=phase,
            )
            return _envelope(
                integration_id=integration_id,
                status="rejected",
                reason_code=RC_INTEGRATE_CONFLICT,
                reason="merge conflict between arc branch and master",
                attempt=attempt,
                duration_s=time.monotonic() - t0,
            )

        gate = await _run_command(
            green_gate_cmd, cwd=worktree_path, timeout=_GATE_TIMEOUT
        )
        if gate.returncode != 0:
            await reset_hard_to(worktree_path, arc_tip_before)
            duration_s = time.monotonic() - t0
            emit_git_integrate_gate_failed(
                integration_id=integration_id,
                arc=arc,
                phase=phase,
                gate_cmd=" ".join(green_gate_cmd),
                gate_exit=gate.returncode,
                duration_s=duration_s,
            )
            return _envelope(
                integration_id=integration_id,
                status="rejected",
                reason_code=RC_GATE_FAILED,
                reason=f"green gate exited {gate.returncode}",
                gate_exit=gate.returncode,
                gate_stdout=gate.stdout,
                gate_stderr=gate.stderr,
                duration_s=duration_s,
            )

        adv = await advance_master_cas(
            source_repo, worktree_path, expected=master_before
        )
        if adv.non_ff:
            # master moved mid-span: reset arc to pre-merge state and retry
            await reset_hard_to(worktree_path, arc_tip_before)
            emit_git_integrate_retried(
                integration_id=integration_id,
                arc=arc,
                attempt=attempt,
                reason="master_advanced",
            )
            continue

        master_sha = adv.new_sha
        merge_commit = merged.merge_commit
        break
    else:
        emit_git_integrate_rejected(
            integration_id=integration_id,
            reason_code=RC_CAS_EXHAUSTED,
            reason=f"CAS failed after {max_attempts} attempts",
            arc=arc,
            phase=phase,
        )
        return _envelope(
            integration_id=integration_id,
            status="rejected",
            reason_code=RC_CAS_EXHAUSTED,
            reason=f"CAS failed after {max_attempts} attempts",
            attempts=max_attempts,
            duration_s=time.monotonic() - t0,
        )

    # 4. OPTIONAL TEARDOWN — non-fatal; integrate is already complete
    teardown_warning = ""
    if remove_worktree:
        err = await _worktree_remove_clean(worktree_path)
        if err:
            teardown_warning = err
            _logger.warning(
                "worktree teardown failed after successful integration",
                extra={"worktree_path": worktree_path, "error": err},
            )

    duration_s = time.monotonic() - t0

    # 5. Emit completion
    emit_git_integrate_completed(
        integration_id=integration_id,
        arc=arc,
        phase=phase,
        merge_commit=merge_commit,
        master_sha=master_sha,
        duration_s=duration_s,
    )

    envelope = _envelope(
        integration_id=integration_id,
        status="completed",
        merge_commit=merge_commit,
        master_sha=master_sha,
        duration_s=duration_s,
    )
    if teardown_warning:
        envelope["teardown_warning"] = teardown_warning
        envelope["reason_code"] = RC_TEARDOWN_FAILED
    return envelope
