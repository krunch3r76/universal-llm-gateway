"""Dispatch op handler — validator → runner → envelope.

Extracted from grok_build.py to keep the top-level entry point thin and to
make room for worktree-op handlers without breaching SLOC.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

from tools._grok_build_envelope import (
    _envelope_rejected,
    _envelope_result,
    _read_only_violation,
)
from tools._grok_build_events import (
    emit_grok_build_dispatch_called,
    emit_grok_build_dispatch_completed,
    emit_grok_build_dispatch_failed,
    emit_grok_build_dispatch_rejected,
    emit_grok_build_dispatch_timeout,
)
from tools._grok_build_registry import release_cwd, try_acquire_cwd
from tools._grok_build_runner import RunnerSpec, run_dispatch
from tools._grok_build_validator import validate_dispatch


async def dispatch_op(
    cwd: str,
    prompt: str,
    *,
    mode: Literal["read_only", "edit"],
    system_context: str | None,
    model: str | None,
    session_id: str | None,
    continue_recent: bool,
    output_format: Literal["json", "streaming-json"],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run the full dispatch flow and return the uniform envelope."""
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    emit_grok_build_dispatch_called(
        dispatch_id=dispatch_id,
        mode=mode,
        op="dispatch",
        session_id=session_id or "",
        model=model or "",
    )

    vr = await asyncio.get_running_loop().run_in_executor(
        None,
        validate_dispatch,
        "dispatch",
        cwd,
        mode,
        session_id,
        continue_recent,
        output_format,
    )
    if not vr.ok:
        emit_grok_build_dispatch_rejected(
            dispatch_id=dispatch_id,
            reason_code=vr.reason_code,
            reason=vr.reason,
            mode=mode,
            op="dispatch",
            cwd=cwd,
            model=model or "",
        )
        return _envelope_rejected(
            dispatch_id, mode, cwd, session_id, model, vr.reason_code, vr.reason
        )

    # Concurrent-dispatch guard — symmetric with session_conflict. A second
    # dispatch into a cwd already in flight rejects without spawning grok.
    if not await try_acquire_cwd(cwd):
        reason = f"another dispatch is already in flight for cwd: {cwd!r}"
        emit_grok_build_dispatch_rejected(
            dispatch_id=dispatch_id,
            reason_code="dispatch_conflict",
            reason=reason,
            mode=mode,
            op="dispatch",
            cwd=cwd,
            model=model or "",
        )
        return _envelope_rejected(
            dispatch_id, mode, cwd, session_id, model, "dispatch_conflict", reason
        )

    try:
        return await _run_and_envelope(
            dispatch_id=dispatch_id,
            t0=t0,
            cwd=cwd,
            prompt=prompt,
            mode=mode,
            system_context=system_context,
            model=model,
            session_id=session_id,
            continue_recent=continue_recent,
            output_format=output_format,
            timeout_seconds=timeout_seconds,
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
    continue_recent: bool,
    output_format: Literal["json", "streaming-json"],
    timeout_seconds: int,
    vr: Any,
) -> dict[str, Any]:
    """Run grok and assemble the completed/failed/timeout envelope.

    Split out of ``dispatch_op`` so the acquire/release try-finally is
    obvious and the registry release is unconditional on the runner path.
    """
    spec = RunnerSpec(
        dispatch_id=dispatch_id,
        cwd=cwd,
        prompt=prompt,
        mode=mode,
        permission_mode=vr.permission_mode,
        system_context=system_context,
        model=model,
        session_id=session_id,
        continue_recent=continue_recent,
        output_format=output_format,
        timeout_seconds=timeout_seconds,
        grok_path=vr.grok_path,
        git_status_pre=vr.git_status_pre,
        dirty_admission=vr.dirty_admission,
    )
    rr = await run_dispatch(spec)
    duration_s = time.monotonic() - t0
    # When read_only admitted a dirty tree, the porcelain delta is
    # indeterminate (can't separate grok's writes from pre-existing). Mark
    # audit_incomplete and suppress the violation flag — caller reads
    # audit_incomplete to know the verdict is unreliable.
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
            **audit,
        )
    elif rr.status == "failed":
        emit_grok_build_dispatch_failed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=rr.exit_code,
            error=(rr.error or rr.stderr)[:200],
            **audit,
        )
    else:
        emit_grok_build_dispatch_timeout(
            dispatch_id=dispatch_id,
            timeout_seconds=timeout_seconds,
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
    )
