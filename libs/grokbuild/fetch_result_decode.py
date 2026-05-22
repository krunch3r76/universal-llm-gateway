"""Sidecar decoding helpers for grokbuild fetch_result."""

from __future__ import annotations

import os
import time
from typing import Any, Literal

from grokbuild.constants import _MODE_BY_PERMISSION
from grokbuild.envelope import _envelope_result, _read_only_violation
from grokbuild.runner import STDOUT_MAX, RunnerResult


def first_record(records: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    return next((r for r in records if r.get("phase") == phase), {})


def last_record(records: list[dict[str, Any]], phase: str) -> dict[str, Any] | None:
    return next((r for r in reversed(records) if r.get("phase") == phase), None)


def started_metadata(started: dict[str, Any]) -> dict[str, Any]:
    """Decode the sidecar ``started`` record into the canonical envelope shape.

    V1 sidecars contain the resolved param surface in the ``started``
    record (see ``grokbuild.runner.run_dispatch``). Falls back to argv
    parsing for fields not present (e.g. when a sidecar was authored by
    a pre-V1 runner — these are unlikely post-deploy but the decode
    must not crash).

    Resume-flag decoding: argv may contain ``-r SESSION`` (strict) or
    ``-s SESSION`` (idempotent). The session_id is captured from
    whichever is present; resume_strict is True iff ``-r`` is in argv.
    """
    argv = started.get("argv") if isinstance(started.get("argv"), list) else []
    permission_mode = str(
        started.get("permission_mode") or _argv_value(argv, "--permission-mode")
    )
    mode = str(started.get("mode") or _mode_from_permission(permission_mode))
    session_id = (
        started.get("session_id")
        or _argv_value(argv, "-r")
        or _argv_value(argv, "-s")
        or _argv_value(argv, "--resume")  # tolerate pre-V1 sidecars
        or None
    )
    return {
        "cwd": str(started.get("cwd") or _argv_value(argv, "--cwd")),
        "mode": mode or "read_only",
        "permission_mode": permission_mode,
        "model": str(started.get("model") or _argv_value(argv, "--model") or ""),
        "session_id": session_id,
        "output_format": str(
            started.get("output_format") or _argv_value(argv, "--output-format")
        ),
        "git_status_pre": str(started.get("git_status_pre") or ""),
        "dirty_admission": bool(started.get("dirty_admission")),
        # V1 surface (zero-valued for sidecars that pre-date the field).
        "tier": str(started.get("tier") or ""),
        "reasoning_effort": started.get("reasoning_effort"),
        "effort": started.get("effort"),
        "check": bool(started.get("check") or "--check" in argv),
        "no_subagents": bool(started.get("no_subagents") or "--no-subagents" in argv),
        "disable_web_search": bool(
            started.get("disable_web_search") or "--disable-web-search" in argv
        ),
        "max_turns": started.get("max_turns"),
        "best_of_n": started.get("best_of_n"),
        "resume_strict": bool(
            started.get("resume_strict")
            if started.get("resume_strict") is not None
            else "-r" in argv
        ),
    }


def terminal_age_seconds(exit_record: dict[str, Any], sidecar_path: str) -> float:
    ts = exit_record.get("ts")
    if isinstance(ts, int | float):
        return max(0.0, time.time() - (float(ts) / 1000.0))
    try:
        return max(0.0, time.time() - os.path.getmtime(sidecar_path))
    except OSError:
        return 0.0


def result_envelope(
    *,
    dispatch_id: str,
    sidecar_path: str,
    records: list[dict[str, Any]],
    started_meta: dict[str, Any],
    exit_record: dict[str, Any],
    result_format: str,
    retention_seconds: int,
) -> dict[str, Any]:
    """Reconstruct the canonical envelope from sidecar records."""
    stdout = _stdout(records)
    stderr = _stderr(records)
    audit_incomplete = bool(exit_record.get("audit_incomplete")) or (
        started_meta["mode"] == "read_only" and started_meta["dirty_admission"]
    )
    read_only_violation = False
    if not audit_incomplete:
        read_only_violation = _read_only_violation(
            started_meta["mode"],
            str(exit_record.get("git_diff_stat") or ""),
            str(exit_record.get("git_status_post") or ""),
        )
    reason_code = str(exit_record.get("reason_code") or "")
    resolved_session_id = exit_record.get("resolved_session_id")
    if not isinstance(resolved_session_id, str):
        resolved_session_id = None
    # Prefer the persisted truncated flag (added after review C2). Fall back to
    # size-based recomputation for sidecars authored before that fix; the
    # recomputed value can be wrong if truncated chunks were silently dropped
    # by a stale decode, but it's the only signal pre-fix sidecars have.
    persisted_truncated = exit_record.get("truncated")
    if isinstance(persisted_truncated, bool):
        truncated = persisted_truncated
    else:
        truncated = len(stdout.encode()) > STDOUT_MAX
    rr = RunnerResult(
        status=_terminal_status(exit_record),
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_record.get("exit_code"),
        duration_s=_duration_s(first_record(records, "started"), exit_record),
        sidecar_path=sidecar_path,
        truncated=truncated,
        git_status_post=str(exit_record.get("git_status_post") or ""),
        git_diff_stat=str(exit_record.get("git_diff_stat") or ""),
        audit_incomplete=audit_incomplete,
        sidecar_gaps=int(exit_record.get("sidecar_gaps") or 0),
        error=stderr[:200],
        dirty_admission=started_meta["dirty_admission"],
        reason_code=reason_code,
        resolved_session_id=resolved_session_id,
    )
    resolved_dict = {
        "tier": started_meta.get("tier", ""),
        "reasoning_effort": started_meta.get("reasoning_effort"),
        "effort": started_meta.get("effort"),
        "check": started_meta.get("check", False),
        "max_turns": started_meta.get("max_turns"),
        "best_of_n": started_meta.get("best_of_n"),
        "timeout_seconds": 0,  # not persisted in started record; safe zero
    }
    out = _envelope_result(
        dispatch_id,
        started_meta["mode"],
        started_meta["cwd"],
        started_meta["session_id"],
        started_meta["model"] or None,
        started_meta["permission_mode"],
        started_meta["git_status_pre"],
        rr,
        read_only_violation,
        audit_incomplete,
        resolved=resolved_dict,
        no_subagents=bool(started_meta.get("no_subagents", False)),
        disable_web_search=bool(started_meta.get("disable_web_search", False)),
        resume_strict=bool(started_meta.get("resume_strict", False)),
    )
    out["metadata"].update(
        format=result_format,
        record_count=len(records),
        output_format=started_meta["output_format"],
        http_status=200,
        retention_seconds=retention_seconds,
    )
    return out


def summary(out: dict[str, Any]) -> dict[str, Any]:
    return {
        "dispatch_id": out["dispatch_id"],
        "status": out["status"],
        "exit_code": out["exit_code"],
        "duration_s": out["duration_s"],
        "cwd": out["metadata"]["cwd"],
        "read_only_violation": out["metadata"]["read_only_violation"],
        "audit_incomplete": out["metadata"]["audit_incomplete"],
        "stdout_preview": out["stdout"][-1000:],
        "stderr_preview": out["stderr"][-1000:],
    }


def text_result(out: dict[str, Any]) -> str:
    lines = [
        f"dispatch_id: {out['dispatch_id']}",
        f"status: {out['status']}",
        f"exit_code: {out['exit_code']}",
        f"cwd: {out['metadata']['cwd']}",
        "",
        "stdout:",
        out["stdout"],
    ]
    if out["stderr"]:
        lines.extend(["", "stderr:", out["stderr"]])
    return "\n".join(lines)


def _argv_value(argv: list[str], flag: str) -> str:
    try:
        idx = argv.index(flag)
    except ValueError:
        return ""
    if idx + 1 >= len(argv):
        return ""
    return argv[idx + 1]


def _mode_from_permission(permission_mode: str) -> str:
    """Reverse the mode → permission_mode map (review W8).

    Falls back to ``"read_only"`` for unknown permission_mode values so
    decode of pre-V1 sidecars (or sidecars authored with a future
    permission_mode) does not crash. The forward map is canonical in
    ``grokbuild.constants._PERMISSION_BY_MODE``; this lookup is derived.
    """
    return _MODE_BY_PERMISSION.get(permission_mode, "read_only")


def _stdout(records: list[dict[str, Any]]) -> str:
    # Include both regular and truncated chunks (review C2). The runner persists
    # over-cap stdout lines under phase "stdout_chunk_truncated" with a `data`
    # field carrying the capped portion; filtering them out silently drops data.
    chunks = [
        str(r.get("data", ""))
        for r in records
        if r.get("phase") in ("stdout_chunk", "stdout_chunk_truncated")
    ]
    if len(chunks) <= 1:
        return "".join(chunks)
    return "\n".join(chunks)


def _stderr(records: list[dict[str, Any]]) -> str:
    return "".join(
        str(r.get("data", ""))
        for r in records
        if r.get("phase") in ("stderr_chunk", "stderr_chunk_truncated")
    )


def _terminal_status(
    exit_record: dict[str, Any],
) -> Literal["completed", "failed", "timeout"]:
    status = exit_record.get("status")
    if status in {"completed", "failed", "timeout"}:
        return status  # type: ignore[return-value]
    exit_code = exit_record.get("exit_code")
    if exit_code is None:
        return "timeout"
    return "completed" if exit_code == 0 else "failed"


def _duration_s(started: dict[str, Any], exit_record: dict[str, Any]) -> float:
    duration = exit_record.get("duration_s")
    if isinstance(duration, int | float):
        return float(duration)
    started_ts = started.get("ts")
    exit_ts = exit_record.get("ts")
    if isinstance(started_ts, int | float) and isinstance(exit_ts, int | float):
        return max(0.0, (float(exit_ts) - float(started_ts)) / 1000.0)
    return 0.0
