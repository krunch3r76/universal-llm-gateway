"""Envelope construction + read_only_violation predicate for grok_build ops.

Shared between dispatch and worktree handlers so every op returns the same
uniform envelope shape — callers can decode without branching on op.
"""

from __future__ import annotations

from typing import Any

from tools._grok_build_runner import RunnerResult


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
        "reason_code": "",
        "reason": "",
        "worktree_name": "",
        "worktree_path": "",
        "branch": "",
        "source_repo": "",
    }


def _envelope_rejected(
    dispatch_id: str,
    mode: str,
    cwd: str,
    session_id: str | None,
    model: str | None,
    reason_code: str,
    reason: str,
) -> dict[str, Any]:
    meta = _metadata_base(mode, cwd, session_id, model)
    meta.update(reason_code=reason_code, reason=reason)
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
