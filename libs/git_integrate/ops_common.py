"""Shared helpers for integrate_op and land_op."""

from __future__ import annotations

import asyncio
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
from git_integrate.git_identity import integrate_git_env_vars
from git_integrate.hub_tree_sync import apply_hub_sync, plan_hub_sync
from git_integrate.schema import (
    RC_CAS_EXHAUSTED,
    RC_DIRTY_MASTER,
    RC_GATE_FAILED,
    RC_INTEGRATE_CONFLICT,
    RC_SUITE_DIGEST_MISMATCH,
    RC_SUITE_DIGEST_UNCOMPUTABLE,
)

_GATE_TIMEOUT = 300.0
_SUITE_DIGEST_TIMEOUT = 1800.0
_SUITE_FIELDS = ("computed", "anchor", "added", "removed")
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


def _suite_digest_reason(stderr: str, gate_exit: int) -> str:
    """Refusal text carrying computed, anchor, added, and removed counts."""
    fields = dict.fromkeys(_SUITE_FIELDS, "?")
    for line in stderr.splitlines():
        key, _, value = line.partition(" ")
        if key in fields and value:
            fields[key] = value.strip()
    counts = " ".join(f"{key}={fields[key]}" for key in _SUITE_FIELDS)
    return f"suite digest gate exited {gate_exit} {counts}"


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
    suite_digest_cmd: list[str] | None = None,
) -> dict[str, Any]:
    """Merge, gate, and CAS-advance master with optimistic retry.

    Returns a success dict with ``master_sha`` and ``merge_commit``, or a
    rejected envelope on failure. After a successful CAS, clean landed
    paths are checked out on the master worktree. Divergent dirt on those
    paths refuses the CAS and reports ``NOT landed@working-tree``.
    """
    for attempt in range(1, max_attempts + 1):
        master_before = await current_sha(source_repo, "refs/heads/master")
        await fetch_master(worktree_path)
        arc_tip_before = await current_sha(worktree_path, "HEAD")

        git_env = integrate_git_env_vars(arc, seat="git-integrate")
        merged = await merge_master_into(worktree_path, git_env=git_env)
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

        candidate = await current_sha(worktree_path, "HEAD")

        async def _refuse_blocked(sync_plan: Any) -> dict[str, Any] | None:
            if not sync_plan.blocked:
                return None
            await reset_hard_to(worktree_path, arc_tip_before)
            reason = "NOT landed@working-tree\n" + sync_plan.porcelain
            emit_git_integrate_rejected(
                integration_id=integration_id,
                reason_code=RC_DIRTY_MASTER,
                reason=reason,
                arc=arc,
                phase=phase,
            )
            return envelope(
                integration_id=integration_id,
                status="rejected",
                reason_code=RC_DIRTY_MASTER,
                reason=reason,
                working_tree="NOT landed@working-tree",
                hub_porcelain=sync_plan.porcelain,
                attempt=attempt,
                duration_s=time.monotonic() - t0,
            )

        plan = await asyncio.to_thread(
            plan_hub_sync, source_repo, master_before, candidate
        )
        refused = await _refuse_blocked(plan)
        if refused is not None:
            return refused

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

        if suite_digest_cmd is not None:
            suite = await _run_command(
                suite_digest_cmd,
                cwd=worktree_path,
                timeout=_SUITE_DIGEST_TIMEOUT,
            )
            if suite.returncode != 0:
                await reset_hard_to(worktree_path, arc_tip_before)
                duration_s = time.monotonic() - t0
                reason_code = (
                    RC_SUITE_DIGEST_MISMATCH
                    if suite.returncode == 2
                    else RC_SUITE_DIGEST_UNCOMPUTABLE
                )
                emit_git_integrate_gate_failed(
                    integration_id=integration_id,
                    arc=arc,
                    phase=phase,
                    gate_cmd=" ".join(suite_digest_cmd),
                    gate_exit=suite.returncode,
                    duration_s=duration_s,
                )
                return envelope(
                    integration_id=integration_id,
                    status="rejected",
                    reason_code=reason_code,
                    reason=_suite_digest_reason(suite.stderr, suite.returncode),
                    gate_exit=suite.returncode,
                    duration_s=duration_s,
                    **_bounded_gate_output(suite.stdout, suite.stderr),
                )

        plan = await asyncio.to_thread(
            plan_hub_sync, source_repo, master_before, candidate
        )
        refused = await _refuse_blocked(plan)
        if refused is not None:
            return refused

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

        working_tree = "no_master_checkout"
        hub_porcelain = ""
        if plan.checkout:
            # Re-planning after CAS treats the stale checkout as dirt: HEAD
            # already names the new blob, so a path we are about to update
            # shows modified. The second plan, above, is the one that runs
            # after the gate and before the ref moves.
            hub_porcelain = await asyncio.to_thread(apply_hub_sync, plan, adv.new_sha)
            working_tree = (
                "NOT landed@working-tree"
                if hub_porcelain.strip()
                else "landed@working-tree"
            )
        return {
            "master_sha": adv.new_sha,
            "merge_commit": merged.merge_commit,
            "attempt": attempt,
            "working_tree": working_tree,
            "hub_porcelain": hub_porcelain,
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
