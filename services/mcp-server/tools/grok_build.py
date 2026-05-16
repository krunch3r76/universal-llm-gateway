"""grok_build MCP tool — thin subprocess wrapper for headless grok CLI dispatch."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, Literal

from tools._grok_build_events import (
    emit_grok_build_dispatch_called,
    emit_grok_build_dispatch_completed,
    emit_grok_build_dispatch_failed,
    emit_grok_build_dispatch_rejected,
    emit_grok_build_dispatch_timeout,
)
from tools._grok_build_runner import RunnerResult, RunnerSpec, run_dispatch
from tools._grok_build_validator import validate_dispatch

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _read_only_violation(
    mode: str,
    git_diff_stat: str,
    git_status_post: str,
) -> bool:
    """Option D audit: True iff read_only AND post-state differs from pre-state.

    Validator (§5.3 check #4) enforces clean pre-state, so any non-empty
    porcelain output is divergence. Porcelain is a strict superset of
    ``git diff --stat`` for change detection — it carries staged, unstaged,
    AND untracked changes in one frame. Reading status_post alone is
    sufficient and catches every YX-coded change including staged-only
    mutations (``M  file``, ``A  file``) that ``git diff --stat`` misses.
    diff_stat is retained as an OR guard for defense in depth (e.g.
    ``audit_incomplete=True`` paths where status_post may be empty but
    diff_stat still has signal).
    """
    if mode != "read_only":
        return False
    return bool(git_diff_stat.strip()) or bool(git_status_post.strip())


def _metadata_base(
    mode: str,
    cwd: str,
    session_id: str | None,
    model: str | None,
    permission_mode: str = "",
    git_status_pre: str = "",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "permission_mode": permission_mode,
        "cwd": cwd,
        "session_id": session_id,
        "model": model,
        "truncated": False,
        "git_status_pre": git_status_pre,
        "git_status_post": "",
        "git_diff_stat": "",
        "read_only_violation": False,
        "audit_incomplete": False,
        "sidecar_gaps": 0,
        "result_delivery_pending": None,
    }


def _envelope_rejected(
    dispatch_id: str,
    mode: str,
    cwd: str,
    session_id: str | None,
    model: str | None,
) -> dict[str, Any]:
    return {
        "dispatch_id": dispatch_id,
        "status": "rejected",
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "duration_s": 0.0,
        "sidecar_path": None,
        "metadata": _metadata_base(mode, cwd, session_id, model),
    }


def _envelope_result(
    dispatch_id: str,
    mode: str,
    cwd: str,
    session_id: str | None,
    model: str | None,
    permission_mode: str,
    git_status_pre: str,
    rr: RunnerResult,
    read_only_violation: bool,
    audit_incomplete: bool,
) -> dict[str, Any]:
    meta = _metadata_base(
        mode,
        cwd,
        session_id,
        model,
        permission_mode=permission_mode,
        git_status_pre=git_status_pre,
    )
    meta.update(
        truncated=rr.truncated,
        git_status_post=rr.git_status_post,
        git_diff_stat=rr.git_diff_stat,
        read_only_violation=read_only_violation,
        audit_incomplete=audit_incomplete,
        sidecar_gaps=rr.sidecar_gaps,
    )
    return {
        "dispatch_id": dispatch_id,
        "status": rr.status,
        "stdout": rr.stdout,
        "stderr": rr.stderr,
        "exit_code": rr.exit_code,
        "duration_s": rr.duration_s,
        "sidecar_path": rr.sidecar_path,
        "metadata": meta,
    }


async def grok_build(
    op: Literal["dispatch"],
    cwd: str,
    prompt: str,
    *,
    mode: Literal["read_only", "edit"] = "read_only",
    system_context: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    continue_recent: bool = False,
    output_format: Literal["json", "streaming-json"] = "json",
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Dispatch headless grok CLI work with intent-labeled + audited modes (Option D)."""
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    emit_grok_build_dispatch_called(
        dispatch_id=dispatch_id,
        mode=mode,
        op=op,
        session_id=session_id or "",
        model=model or "",
    )

    vr = await asyncio.get_running_loop().run_in_executor(
        None,
        validate_dispatch,
        op,
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
            op=op,
            cwd=cwd,
            model=model or "",
        )
        return _envelope_rejected(dispatch_id, mode, cwd, session_id, model)

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


def register_grok_build_tools(mcp: FastMCP) -> None:
    """Mount grok_build on the MCP catalog (decoration-at-register-time)."""
    mcp.tool(title="Grok Build Dispatch")(grok_build)
