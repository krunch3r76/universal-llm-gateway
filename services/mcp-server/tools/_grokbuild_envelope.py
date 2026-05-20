"""Envelope construction + read_only_violation predicate for grokbuild ops.

Shared between dispatch and worktree handlers so every op returns the same
uniform envelope shape — callers can decode without branching on op.
V1 metadata surface (tier overlay output, resolved_session_id, reason_code)
is documented on ``_metadata_base``.
"""

from __future__ import annotations

from typing import Any

from tools._grokbuild_runner import RunnerResult


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
    """Build the metadata block shared by every envelope shape.

    V1 additions (zero-valued for rejected envelopes; populated by
    ``_envelope_result`` for run envelopes):

    * ``tier`` — resolved tier (string) or "" when not resolved
    * ``reasoning_effort`` / ``effort`` — resolved scalars or ""
    * ``check`` / ``no_subagents`` / ``disable_web_search`` /
      ``resume_strict`` — booleans (default False)
    * ``max_turns`` / ``best_of_n`` — ints or None (default None)
    * ``timeout_seconds`` — resolved int or 0
    * ``resolved_session_id`` — captured from streaming-json stdout or None
    """
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
        # Set non-null only on dispatch_conflict rejections so the caller
        # can recover via fetch_result(dispatch_id) without sidecar grep.
        "conflicting_dispatch_id": None,
        # V1 resolved param surface (zero-valued by default).
        "tier": "",
        "reasoning_effort": "",
        "effort": "",
        "check": False,
        "no_subagents": False,
        "disable_web_search": False,
        "resume_strict": False,
        "max_turns": None,
        "best_of_n": None,
        "timeout_seconds": 0,
        "resolved_session_id": None,
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
    """Read a field from either a ``_ResolvedParams`` dataclass or a dict-shaped substitute.

    The dispatcher path passes the dataclass directly; the
    ``fetch_result_decode`` path passes a dict reconstructed from the
    sidecar ``started`` record. Both paths share this single accessor so
    the envelope writer stays decoupled from the resolver's exact type.
    """
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
    permission_mode: str,
    git_status_pre: str,
    rr: RunnerResult,
    read_only_violation: bool,
    audit_incomplete: bool,
    *,
    resolved: Any = None,
    no_subagents: bool = False,
    disable_web_search: bool = False,
    resume_strict: bool = False,
) -> dict[str, Any]:
    """Assemble the canonical envelope for a completed/failed/timeout run.

    Keyword args default to safe zero values so ``fetch_result_decode``
    (which reconstructs envelopes from sidecars) can call this with the
    decode-time resolved values without forcing every call site to thread
    the V1 surface. The dispatcher always supplies ``resolved`` (the
    ``_ResolvedParams`` dataclass from ``_grokbuild_dispatch``); the
    decode path supplies a dict with the same field names.
    """
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
    meta.update(
        reason_code=rr.reason_code,
        resolved_session_id=rr.resolved_session_id,
        no_subagents=no_subagents,
        disable_web_search=disable_web_search,
        resume_strict=resume_strict,
    )
    if resolved is not None:
        meta.update(
            tier=_resolved_get(resolved, "tier", ""),
            reasoning_effort=_resolved_get(resolved, "reasoning_effort", ""),
            effort=_resolved_get(resolved, "effort", ""),
            check=bool(_resolved_get(resolved, "check", False)),
            max_turns=_resolved_get(resolved, "max_turns", None),
            best_of_n=_resolved_get(resolved, "best_of_n", None),
            timeout_seconds=int(_resolved_get(resolved, "timeout_seconds", 0)),
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
