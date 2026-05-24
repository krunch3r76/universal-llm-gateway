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

from grokbuild.constants import (
    DEFAULT_TIMEOUT_SECONDS,
    _TIER_PRESETS,
    _VALID_TIERS,
    default_model_for_tier,
)
from grokbuild.envelope import (
    _envelope_rejected,
    _envelope_result,
    _read_only_violation,
)
from grokbuild.events import (
    emit_grok_build_dispatch_called,
    emit_grok_build_dispatch_completed,
    emit_grok_build_dispatch_failed,
    emit_grok_build_dispatch_rejected,
    emit_grok_build_dispatch_timeout,
    emit_grok_build_dispatch_tool_calls,
    emit_grok_build_dispatch_zero_tool_calls_when_expected,
)
from grokbuild.registry import (
    get_dispatch_id,
    release_cwd,
    try_acquire_cwd,
)
from grokbuild.runner import RunnerSpec, run_dispatch
from grokbuild.validator import ValidationResult, validate_dispatch


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
    timeout_seconds: int | None
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

    Caller responsibility: ``tier`` MUST be in _TIER_PRESETS. ``dispatch_op``
    enforces this pre-resolve so a bad tier produces the structured
    rejected envelope rather than a KeyError; direct callers (tests) must
    pre-validate. reasoning_effort/effort: explicit value wins over preset.
    timeout_seconds: explicit int wins; omitted None → DEFAULT_TIMEOUT_SECONDS;
    0 → None (no wall-clock limit). max_turns/best_of_n: opt-in only;
    explicit None means "do not include the grok flag".
    """
    preset = _TIER_PRESETS[tier]
    return _ResolvedParams(
        tier=tier,
        reasoning_effort=reasoning_effort
        if reasoning_effort is not None
        else preset.reasoning_effort,
        effort=effort if effort is not None else preset.effort,
        timeout_seconds=(
            None
            if timeout_seconds == 0
            else (
                timeout_seconds
                if timeout_seconds is not None
                else DEFAULT_TIMEOUT_SECONDS
            )
        ),
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
    dispatch_id: str | None = None,
    proc_pid_holder: list[int] | None = None,
    recursion_depth: int | None = None,
) -> dict[str, Any]:
    """Run the full dispatch flow and return the uniform envelope.

    Phase B (V2): when ``dispatch_id`` is supplied the caller has already
    minted the id (used by ``GrokbuildExecutionTracker`` to return the id
    in the 202 response before the dispatch completes). When
    ``proc_pid_holder`` is supplied the runner appends the subprocess pid
    to it so the tracker can cancel via SIGTERM/SIGKILL.
    """
    if dispatch_id is None:
        dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()

    # NOTE (review G7): ``.called`` fires AFTER admission — both the
    # validator pass and the registry acquire — so rejected dispatches
    # emit only ``.rejected``. The architecture invariant (see
    # test_edit_working_tree_dirty_rejection) is that rejected events
    # carry their own correlation fields (mode/op/cwd/model); they do
    # NOT depend on joining via ``.called`` → dispatch_id.

    # Pre-resolve admission: _resolve_params indexes _TIER_PRESETS[tier]
    # and would raise KeyError on bad input, propagating as dispatch_crashed
    # instead of the structured rejected envelope. Validator's bad_tier
    # check (validator.py §2) is unreachable for this case because tier
    # overlay runs first; this guard restores the structured rejection
    # contract before _resolve_params is called.
    if tier not in _VALID_TIERS:
        reason = f"tier must be one of {sorted(_VALID_TIERS)!r}, got {tier!r}"
        emit_grok_build_dispatch_rejected(
            dispatch_id=dispatch_id,
            reason_code="bad_tier",
            reason=reason,
            mode=mode,
            op="build",
            cwd=cwd,
            model=model or "",
        )
        return _envelope_rejected(
            dispatch_id, mode, cwd, session_id, model, "bad_tier", reason
        )

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
    if model is None:
        model = default_model_for_tier(resolved.tier)

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
            proc_pid_holder=proc_pid_holder,
            recursion_depth=recursion_depth,
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
    proc_pid_holder: list[int] | None = None,
    recursion_depth: int | None = None,
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
        proc_pid_holder=proc_pid_holder,
        recursion_depth=recursion_depth,
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
            timeout_seconds=spec.timeout_seconds or 0,
            cwd=cwd,
            **audit,
        )

    # C.1(ii): emit tool-calls summary after every dispatch with parsed stdout.
    # tool_call_names is empty on spawn-failed / timeout / dispatch_home failures;
    # those paths never reach communicate() so there is no stdout to parse.
    if rr.tool_call_names or rr.status in {"completed", "failed"}:
        _names = list(rr.tool_call_names)
        emit_grok_build_dispatch_tool_calls(
            dispatch_id=dispatch_id,
            tool_count=len(_names),
            tool_names=_names,
        )
        # Anomaly: edit-mode dispatch with zero tool calls is unexpected.
        if len(_names) == 0 and mode == "edit" and rr.status == "completed":
            emit_grok_build_dispatch_zero_tool_calls_when_expected(
                dispatch_id=dispatch_id,
                mode=mode,
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
