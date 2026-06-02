"""Uniform dispatch envelope for cursorbuild ops."""

from __future__ import annotations

from typing import Any

from cursorbuild.runner_types import RunnerResult


def _read_only_violation(
    mode: str,
    git_diff_stat: str,
    git_status_post: str,
) -> bool:
    if mode != "read_only":
        return False
    return bool(git_diff_stat.strip()) or bool(git_status_post.strip())


def _metadata_base(
    mode: str,
    cwd: str,
    session_id: str | None,
    model: str | None,
    *,
    read_only_mode: str = "plan",
    git_status_pre: str = "",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "read_only_mode": read_only_mode,
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
        "reason_code": "",
        "reason": "",
        "conflicting_dispatch_id": None,
        "tier": "",
        "timeout_seconds": 0,
        "resolved_session_id": None,
        "mcp_enabled": False,
    }


def _envelope_rejected(
    dispatch_id: str,
    mode: str,
    cwd: str,
    session_id: str | None,
    model: str | None,
    reason_code: str,
    reason: str,
    *,
    conflicting_dispatch_id: str | None = None,
) -> dict[str, Any]:
    meta = _metadata_base(mode, cwd, session_id, model)
    meta.update(reason_code=reason_code, reason=reason)
    if conflicting_dispatch_id:
        meta["conflicting_dispatch_id"] = conflicting_dispatch_id
    return {
        "dispatch_id": dispatch_id,
        "status": "rejected",
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "duration_s": 0.0,
        "sidecar_path": None,
        "metadata": meta,
    }


def _resolved_get(resolved: Any, name: str, default: Any) -> Any:
    if hasattr(resolved, name):
        return getattr(resolved, name)
    if isinstance(resolved, dict):
        return resolved.get(name, default)
    return default


def _envelope_result(
    dispatch_id: str,
    mode: str,
    cwd: str,
    session_id: str | None,
    model: str | None,
    read_only_mode: str,
    git_status_pre: str,
    rr: RunnerResult,
    read_only_violation: bool,
    audit_incomplete: bool,
    *,
    resolved: Any = None,
    mcp_enabled: bool = False,
) -> dict[str, Any]:
    meta = _metadata_base(
        mode,
        cwd,
        session_id,
        model,
        read_only_mode=read_only_mode,
        git_status_pre=git_status_pre,
    )
    meta.update(
        truncated=rr.truncated,
        git_status_post=rr.git_status_post,
        git_diff_stat=rr.git_diff_stat,
        read_only_violation=read_only_violation,
        audit_incomplete=audit_incomplete,
        sidecar_gaps=rr.sidecar_gaps,
        reason_code=rr.reason_code,
        resolved_session_id=rr.resolved_session_id,
        mcp_enabled=mcp_enabled,
    )
    if resolved is not None:
        meta.update(
            tier=_resolved_get(resolved, "tier", ""),
            timeout_seconds=int(_resolved_get(resolved, "timeout_seconds", 0) or 0),
            model=_resolved_get(resolved, "model", model),
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
