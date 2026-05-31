"""Shared helpers for integrate_op and land_op."""

from __future__ import annotations

import time
from typing import Any

from universal_logging import get_logger

from git_integrate.events import (
    emit_git_integrate_gate_failed,
    emit_git_integrate_rejected,
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
)

_GATE_TIMEOUT = 300.0
_GATE_OUTPUT_TAIL_LINES = 20
_logger = get_logger(__name__)


def _bounded_gate_output(stdout: str, stderr: str) -> dict[str, Any]:
    """Bounded gate output for the rejection envelope.

    The green gate can emit very large output (a ruff run over a big changeset
    produced ~895KB once). Returning it inline floods caller context, so the
    envelope carries only a line count plus the trailing lines — where ruff's
    ``Found N errors.`` summary lands. Full output is recoverable by re-running
    the gate locally; it is never inlined by default.
    """
    parts = [p for p in (stdout, stderr) if p]
    lines = "\n".join(parts).splitlines()
    return {
        "gate_output_line_count": len(lines),
        "gate_output_tail": "\n".join(lines[-_GATE_OUTPUT_TAIL_LINES:]),
    }


def envelope(
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


async def worktree_remove_clean(worktree_path: str) -> str:
    """Remove the worktree after successful integration.

    Non-fatal: returns error string on failure.
    """
    proc = await _run_command(
        ["git", "-C", worktree_path, "worktree", "remove", worktree_path],
        timeout=30.0,
    )
    if proc.returncode != 0:
        return proc.stderr.strip() or "git worktree remove failed"
    return ""


async def integrate_retry_loop(
    *,
    integration_id: str,
    arc: str,
    phase: str,
    worktree_path: str,
    source_repo: str,
    green_gate_cmd: list[str],
    max_attempts: int,
    t0: float,
) -> dict[str, Any]:
    """Merge, gate, and CAS-advance master with optimistic retry.

    Returns a success dict with ``master_sha`` and ``merge_commit``, or a
    rejected envelope on failure.
    """
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
            return envelope(
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
            return envelope(
                integration_id=integration_id,
                status="rejected",
                reason_code=RC_GATE_FAILED,
                reason=f"green gate exited {gate.returncode}",
                gate_exit=gate.returncode,
                duration_s=duration_s,
                **_bounded_gate_output(gate.stdout, gate.stderr),
            )

        adv = await advance_master_cas(
            source_repo, worktree_path, expected=master_before
        )
        if adv.non_ff:
            await reset_hard_to(worktree_path, arc_tip_before)
            emit_git_integrate_retried(
                integration_id=integration_id,
                arc=arc,
                attempt=attempt,
                reason="master_advanced",
            )
            continue

        return {
            "master_sha": adv.new_sha,
            "merge_commit": merged.merge_commit,
            "attempt": attempt,
        }

    emit_git_integrate_rejected(
        integration_id=integration_id,
        reason_code=RC_CAS_EXHAUSTED,
        reason=f"CAS failed after {max_attempts} attempts",
        arc=arc,
        phase=phase,
    )
    return envelope(
        integration_id=integration_id,
        status="rejected",
        reason_code=RC_CAS_EXHAUSTED,
        reason=f"CAS failed after {max_attempts} attempts",
        attempts=max_attempts,
        duration_s=time.monotonic() - t0,
    )
