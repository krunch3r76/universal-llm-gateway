"""Dispatch op — validator → registry → runner → envelope."""

from __future__ import annotations

import asyncio
import functools
import time
import uuid
from typing import Any, Literal

from cursorbuild.constants import _VALID_TIERS
from cursorbuild.dispatch_helpers import _resolve_params, _ResolvedParams
from cursorbuild.envelope import (
    _envelope_rejected,
    _envelope_result,
    _read_only_violation,
)
from cursorbuild.lifecycle import enrich_system_context_for_boot
from cursorbuild.registry import get_dispatch_id, release_cwd, try_acquire_cwd
from cursorbuild.runner import run_dispatch
from cursorbuild.runner_types import RunnerSpec
from cursorbuild.validator import ValidationResult, validate_dispatch


async def dispatch_op(
    cwd: str,
    prompt: str,
    *,
    mode: Literal["read_only", "edit"],
    system_context: str | None,
    model: str | None,
    session_id: str | None,
    continue_session: bool = False,
    tier: str = "default",
    timeout_seconds: int | None = None,
    read_only_mode: str = "plan",
    mcp_enabled: bool = False,
    force: bool = False,
    stream_partial_output: bool = False,
    worktree_name: str | None = None,
    worktree_base: str | None = None,
    skip_worktree_setup: bool = False,
    dispatch_id: str | None = None,
    proc_pid_holder: list[int] | None = None,
    recursion_depth: int | None = None,
    boot_agent: str = "claude-cursor",
) -> dict[str, Any]:
    if dispatch_id is None:
        dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()

    if tier not in _VALID_TIERS:
        reason = f"tier must be one of {sorted(_VALID_TIERS)!r}, got {tier!r}"
        return _envelope_rejected(
            dispatch_id, mode, cwd, session_id, model, "bad_tier", reason
        )

    resolved = _resolve_params(tier=tier, model=model, timeout_seconds=timeout_seconds)

    vr = await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(
            validate_dispatch,
            cwd=cwd,
            mode=mode,
            session_id=session_id,
            continue_session=continue_session,
            tier=resolved.tier,
            timeout_seconds=resolved.timeout_seconds,
            read_only_mode=read_only_mode,
            mcp_enabled=mcp_enabled,
            prompt=prompt,
        ),
    )
    if not vr.ok:
        return _envelope_rejected(
            dispatch_id, mode, cwd, session_id, model, vr.reason_code, vr.reason
        )

    if not await try_acquire_cwd(cwd, dispatch_id, mode=mode):
        conflicting = await get_dispatch_id(cwd)
        reason = f"another dispatch is already in flight for cwd: {cwd!r}"
        return _envelope_rejected(
            dispatch_id,
            mode,
            cwd,
            session_id,
            model,
            "dispatch_conflict",
            reason,
            conflicting_dispatch_id=conflicting,
        )

    try:
        system_context = enrich_system_context_for_boot(
            system_context,
            agent=boot_agent,
            dispatch_id=dispatch_id,
        )
        return await _run_and_envelope(
            dispatch_id=dispatch_id,
            t0=t0,
            cwd=cwd,
            prompt=prompt,
            mode=mode,
            system_context=system_context,
            model=resolved.model,
            session_id=session_id,
            continue_session=continue_session,
            resolved=resolved,
            vr=vr,
            mcp_enabled=mcp_enabled,
            force=force,
            stream_partial_output=stream_partial_output,
            worktree_name=worktree_name,
            worktree_base=worktree_base,
            skip_worktree_setup=skip_worktree_setup,
            proc_pid_holder=proc_pid_holder,
            recursion_depth=recursion_depth,
        )
    finally:
        await release_cwd(cwd, dispatch_id)


async def _run_and_envelope(
    *,
    dispatch_id: str,
    t0: float,
    cwd: str,
    prompt: str,
    mode: Literal["read_only", "edit"],
    system_context: str | None,
    model: str,
    session_id: str | None,
    continue_session: bool,
    resolved: _ResolvedParams,
    vr: ValidationResult,
    mcp_enabled: bool,
    force: bool,
    stream_partial_output: bool,
    worktree_name: str | None,
    worktree_base: str | None,
    skip_worktree_setup: bool,
    proc_pid_holder: list[int] | None,
    recursion_depth: int | None,
) -> dict[str, Any]:
    spec = RunnerSpec(
        dispatch_id=dispatch_id,
        cwd=cwd,
        prompt=prompt,
        mode=mode,
        cursor_agent_bin=vr.cursor_agent_bin,
        model=model,
        system_context=system_context,
        session_id=session_id,
        timeout_seconds=resolved.timeout_seconds,
        tier=resolved.tier,  # type: ignore[arg-type]
        read_only_mode=vr.read_only_mode,
        mcp_enabled=mcp_enabled,
        force=force,
        continue_session=continue_session,
        worktree_name=worktree_name,
        worktree_base=worktree_base,
        skip_worktree_setup=skip_worktree_setup,
        stream_partial_output=stream_partial_output,
        recursion_depth=recursion_depth,
        git_status_pre=vr.git_status_pre,
        dirty_admission=vr.dirty_admission,
        proc_pid_holder=proc_pid_holder,
    )
    rr = await run_dispatch(spec)
    audit_incomplete = rr.audit_incomplete or (
        spec.mode == "read_only" and spec.dirty_admission
    )
    if audit_incomplete:
        violation = False
    else:
        violation = _read_only_violation(mode, rr.git_diff_stat, rr.git_status_post)

    return _envelope_result(
        dispatch_id,
        mode,
        cwd,
        session_id,
        model,
        vr.read_only_mode,
        spec.git_status_pre,
        rr,
        violation,
        audit_incomplete,
        resolved=resolved,
        mcp_enabled=mcp_enabled,
    )
