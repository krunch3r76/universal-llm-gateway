"""Dispatch op handler — validator → runner → envelope.

Extracted from grokbuild.py to keep the top-level entry point thin and to
make room for worktree-op handlers without breaching SLOC.
"""

from __future__ import annotations

import asyncio
import functools
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from tools._grokbuild_constants import _TIER_PRESETS
from tools._grokbuild_envelope import (
    _envelope_rejected,
    _envelope_result,
    _read_only_violation,
)
from tools._grokbuild_events import (
    emit_grok_build_dispatch_called,
    emit_grok_build_dispatch_completed,
    emit_grok_build_dispatch_failed,
    emit_grok_build_dispatch_rejected,
    emit_grok_build_dispatch_timeout,
)
from tools._grokbuild_registry import (
    get_dispatch_id,
    release_cwd,
    try_acquire_cwd,
)
from tools._grokbuild_runner import RunnerSpec, run_dispatch
from tools._grokbuild_validator import ValidationResult, validate_dispatch


def _resolve_check(
    *,
    mode: Literal["read_only", "edit"],
    explicit: bool | None,
) -> bool:
    """Mode-aware check resolution.

    Explicit True/False wins. None resolves to True for mode='edit',
    False for mode='read_only' (per plan §TIER × MODE × CHECK).
    """
    if explicit is not None:
        return explicit
    return mode == "edit"


@dataclass(frozen=True, slots=True)
class _ResolvedParams:
    """Tier-overlay + explicit-override resolution output.

    Every field is a concrete scalar. None on reasoning_effort/effort/
    max_turns/best_of_n means "do not emit the corresponding grok CLI
    flag at all" (caller chose to skip explicitly).
    """

    tier: str
    reasoning_effort: str
    effort: str
    timeout_seconds: int
    check: bool
    max_turns: int | None
    best_of_n: int | None


def _resolve_params(
    *,
    tier: str,
    reasoning_effort: str | None,
    effort: str | None,
    timeout_seconds: int | None,
    check: bool | None,
    max_turns: int | None,
    best_of_n: int | None,
    mode: Literal["read_only", "edit"],
) -> _ResolvedParams:
    """Apply tier preset, then per-param explicit overrides.

    Caller responsibility: ``tier`` MUST be in _TIER_PRESETS (validator
    enforces). reasoning_effort/effort/timeout_seconds: explicit value
    wins over preset; None reverts to preset. max_turns/best_of_n: opt-in
    only; explicit None means "do not include the grok flag".
    """
    preset = _TIER_PRESETS[tier]
    return _ResolvedParams(
        tier=tier,
        reasoning_effort=reasoning_effort if reasoning_effort is not None else preset.reasoning_effort,
        effort=effort if effort is not None else preset.effort,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else preset.timeout_seconds,
        check=_resolve_check(mode=mode, explicit=check),
        max_turns=max_turns,
        best_of_n=best_of_n,
    )


async def dispatch_op(
    cwd: str,
    prompt: str,
    *,
    mode: Literal["read_only", "edit"],
    system_context: str | None,
    model: str | None,
    session_id: str | None,
    continue_recent: bool,  # always False in production; validator rejects True
    output_format: str,  # broadened per design rationale §1
    timeout_seconds: int | None,
    tier: str,
    reasoning_effort: str | None,
    effort: str | None,
    check: bool | None,
    no_subagents: bool,
    disable_web_search: bool,
    max_turns: int | None,
    best_of_n: int | None,
    resume_strict: bool,
) -> dict[str, Any]:
    """Run the full dispatch flow and return the uniform envelope."""
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()

    # NOTE (review G7): ``.called`` fires AFTER admission — both the
    # validator pass and the registry acquire — so rejected dispatches
    # emit only ``.rejected``. The architecture invariant (see
    # test_edit_working_tree_dirty_rejection) is that rejected events
    # carry their own correlation fields (mode/op/cwd/model); they do
    # NOT depend on joining via ``.called`` → dispatch_id.

    # Tier overlay BEFORE validator so range checks see resolved scalars.
    resolved = _resolve_params(
        tier=tier,
        reasoning_effort=reasoning_effort,
        effort=effort,
        timeout_seconds=timeout_seconds,
        check=check,
        max_turns=max_turns,
        best_of_n=best_of_n,
        mode=mode,
    )

    vr = await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(
            validate_dispatch,
            op="build",
            cwd=cwd,
            mode=mode,
            session_id=session_id,
            continue_recent=continue_recent,
            output_format=output_format,
            # Pass resolved values so the validator sees the actually-used
            # scalars after tier overlay (review W1). Previously only
            # timeout_seconds was post-resolution, creating an asymmetric
            # gap where the validator never validated the values the
            # runner would receive when the caller omitted them.
            tier=resolved.tier,
            reasoning_effort=resolved.reasoning_effort,
            effort=resolved.effort,
            max_turns=resolved.max_turns,
            best_of_n=resolved.best_of_n,
            timeout_seconds=resolved.timeout_seconds,
            resume_strict=resume_strict,
        ),
    )
    if not vr.ok:
        emit_grok_build_dispatch_rejected(
            dispatch_id=dispatch_id,
            reason_code=vr.reason_code,
            reason=vr.reason,
            mode=mode,
            op="build",
            cwd=cwd,
            model=model or "",
        )
        return _envelope_rejected(
            dispatch_id, mode, cwd, session_id, model, vr.reason_code, vr.reason
        )

    if not await try_acquire_cwd(cwd, dispatch_id):
        conflicting = await get_dispatch_id(cwd)
        reason = f"another dispatch is already in flight for cwd: {cwd!r}"
        emit_grok_build_dispatch_rejected(
            dispatch_id=dispatch_id,
            reason_code="dispatch_conflict",
            reason=reason,
            mode=mode,
            op="build",
            cwd=cwd,
            model=model or "",
        )
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
        emit_grok_build_dispatch_called(
            dispatch_id=dispatch_id,
            mode=mode,
            op="build",
            session_id=session_id or "",
            model=model or "",
        )
        return await _run_and_envelope(
            dispatch_id=dispatch_id,
            t0=t0,
            cwd=cwd,
            prompt=prompt,
            mode=mode,
            system_context=system_context,
            model=model,
            session_id=session_id,
            output_format=output_format,
            resolved=resolved,
            no_subagents=no_subagents,
            disable_web_search=disable_web_search,
            resume_strict=resume_strict,
            vr=vr,
        )
    finally:
        await release_cwd(cwd)


async def _run_and_envelope(
    *,
    dispatch_id: str,
    t0: float,
    cwd: str,
    prompt: str,
    mode: Literal["read_only", "edit"],
    system_context: str | None,
    model: str | None,
    session_id: str | None,
    output_format: str,
    resolved: _ResolvedParams,
    no_subagents: bool,
    disable_web_search: bool,
    resume_strict: bool,
    vr: ValidationResult,
) -> dict[str, Any]:
    """Run grok and assemble the completed/failed/timeout envelope."""
    spec = RunnerSpec(
        dispatch_id=dispatch_id,
        cwd=cwd,
        prompt=prompt,
        mode=mode,
        permission_mode=vr.permission_mode,
        system_context=system_context,
        model=model,
        session_id=session_id,
        timeout_seconds=resolved.timeout_seconds,
        grok_path=vr.grok_path,
        git_status_pre=vr.git_status_pre,
        tier=resolved.tier,  # type: ignore[arg-type]
        reasoning_effort=resolved.reasoning_effort,
        effort=resolved.effort,
        check=resolved.check,
        no_subagents=no_subagents,
        disable_web_search=disable_web_search,
        max_turns=resolved.max_turns,
        best_of_n=resolved.best_of_n,
        resume_strict=resume_strict,
        dirty_admission=vr.dirty_admission,
    )
    rr = await run_dispatch(spec)
    duration_s = time.monotonic() - t0
    audit_incomplete = rr.audit_incomplete or (
        spec.mode == "read_only" and rr.dirty_admission
    )
    if audit_incomplete:
        violation = False
    else:
        violation = _read_only_violation(mode, rr.git_diff_stat, rr.git_status_post)
    audit = {
        "git_status_pre": spec.git_status_pre,
        "git_status_post": rr.git_status_post,
        "git_diff_stat": rr.git_diff_stat,
        "read_only_violation": violation,
        "audit_incomplete": audit_incomplete,
        "sidecar_gaps": rr.sidecar_gaps,
    }

    if rr.status == "completed":
        emit_grok_build_dispatch_completed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=rr.exit_code or 0,
            truncated=rr.truncated,
            cwd=cwd,
            **audit,
        )
    elif rr.status == "failed":
        emit_grok_build_dispatch_failed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=rr.exit_code,
            error=(rr.error or rr.stderr)[:200],
            reason_code=rr.reason_code,
            cwd=cwd,
            **audit,
        )
    else:
        emit_grok_build_dispatch_timeout(
            dispatch_id=dispatch_id,
            timeout_seconds=spec.timeout_seconds,
            cwd=cwd,
            **audit,
        )
    return _envelope_result(
        dispatch_id,
        mode,
        cwd,
        session_id,
        model,
        vr.permission_mode,
        spec.git_status_pre,
        rr,
        violation,
        audit_incomplete,
        resolved=resolved,
        no_subagents=no_subagents,
        disable_web_search=disable_web_search,
        resume_strict=resume_strict,
    )
