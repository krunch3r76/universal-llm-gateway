"""Sidecar decoding helpers for cursorbuild fetch_result (argv-bound)."""

from __future__ import annotations

import os
import time
from typing import Any

from cursorbuild.constants import OUTPUT_FORMAT
from cursorbuild.envelope import _envelope_result, _read_only_violation
from cursorbuild.runner import STDOUT_MAX, RunnerResult


def first_record(records: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    return next((r for r in records if r.get("phase") == phase), {})


def last_record(records: list[dict[str, Any]], phase: str) -> dict[str, Any] | None:
    return next((r for r in reversed(records) if r.get("phase") == phase), None)


def _argv_value(argv: list[str], flag: str) -> str:
    if flag not in argv:
        return ""
    idx = argv.index(flag)
    if idx + 1 >= len(argv):
        return ""
    return argv[idx + 1]


def started_metadata(started: dict[str, Any]) -> dict[str, Any]:
    argv = started.get("argv") if isinstance(started.get("argv"), list) else []
    mode = str(started.get("mode") or "read_only")
    read_only_mode = str(
        started.get("read_only_mode") or _argv_value(argv, "--mode") or "plan"
    )
    session_id = started.get("session_id") or _argv_value(argv, "--resume") or None
    if "--continue" in argv and not session_id:
        session_id = started.get("session_id")
    return {
        "cwd": str(started.get("cwd") or _argv_value(argv, "--workspace")),
        "mode": mode,
        "read_only_mode": read_only_mode,
        "model": str(started.get("model") or _argv_value(argv, "--model") or ""),
        "session_id": session_id,
        "output_format": str(
            started.get("output_format") or _argv_value(argv, "--output-format")
        ),
        "git_status_pre": str(started.get("git_status_pre") or ""),
        "dirty_admission": bool(started.get("dirty_admission")),
        "tier": str(started.get("tier") or ""),
        "timeout_seconds": started.get("timeout_seconds"),
        "mcp_enabled": bool(started.get("mcp_enabled")),
    }


def terminal_age_seconds(exit_record: dict[str, Any], sidecar_path: str) -> float:
    ts = exit_record.get("ts")
    if isinstance(ts, int | float):
        return max(0.0, time.time() - (float(ts) / 1000.0))
    try:
        return max(0.0, time.time() - os.path.getmtime(sidecar_path))
    except OSError:
        return 0.0


def _terminal_status(exit_record: dict[str, Any]) -> str:
    return str(exit_record.get("status") or "failed")


def _duration_s(started: dict[str, Any], exit_record: dict[str, Any]) -> float:
    if "duration_s" in exit_record:
        return float(exit_record["duration_s"])
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
    stdout_chunks = [
        str(r.get("data") or "")
        for r in records
        if r.get("phase") in ("stdout_chunk", "stdout_chunk_truncated")
    ]
    stderr_chunks = [
        str(r.get("data") or "")
        for r in records
        if r.get("phase") in ("stderr_chunk", "stderr_chunk_truncated")
    ]
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    truncated = bool(exit_record.get("truncated")) or len(stdout.encode()) > STDOUT_MAX
    if len(stdout.encode()) > STDOUT_MAX:
        stdout = stdout[-STDOUT_MAX:]
        truncated = True

    reason_code = str(exit_record.get("reason_code") or "")
    rr = RunnerResult(
        status=_terminal_status(exit_record),  # type: ignore[arg-type]
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_record.get("exit_code"),
        duration_s=_duration_s(started_meta, exit_record),
        sidecar_path=sidecar_path,
        truncated=truncated,
        git_status_post=str(exit_record.get("git_status_post") or ""),
        git_diff_stat=str(exit_record.get("git_diff_stat") or ""),
        audit_incomplete=bool(exit_record.get("audit_incomplete")),
        sidecar_gaps=int(exit_record.get("sidecar_gaps") or 0),
        error=str(exit_record.get("error") or stderr[:200]),
        reason_code=reason_code,
        resolved_session_id=exit_record.get("resolved_session_id"),
    )
    mode = started_meta["mode"]
    violation = _read_only_violation(mode, rr.git_diff_stat, rr.git_status_post)
    audit_incomplete = rr.audit_incomplete or (
        mode == "read_only" and started_meta.get("dirty_admission")
    )
    out = _envelope_result(
        dispatch_id,
        mode,
        started_meta["cwd"],
        started_meta.get("session_id"),
        started_meta.get("model") or None,
        started_meta.get("read_only_mode", "plan"),
        started_meta.get("git_status_pre", ""),
        rr,
        violation,
        audit_incomplete,
        resolved={
            "tier": started_meta.get("tier", ""),
            "timeout_seconds": started_meta.get("timeout_seconds") or 0,
            "model": started_meta.get("model"),
        },
        mcp_enabled=bool(started_meta.get("mcp_enabled")),
    )
    out["metadata"].update(
        format=result_format,
        record_count=len(records),
        output_format=started_meta.get("output_format") or OUTPUT_FORMAT,
        http_status=200,
        retention_seconds=retention_seconds,
    )
    return out


def summary(out: dict[str, Any]) -> dict[str, Any]:
    meta = out.get("metadata", {})
    return {
        "dispatch_id": out.get("dispatch_id"),
        "status": out.get("status"),
        "exit_code": out.get("exit_code"),
        "duration_s": out.get("duration_s"),
        "reason_code": meta.get("reason_code"),
        "resolved_session_id": meta.get("resolved_session_id"),
    }


def text_result(out: dict[str, Any]) -> dict[str, Any]:
    return {"text": out.get("stdout") or out.get("stderr") or ""}
