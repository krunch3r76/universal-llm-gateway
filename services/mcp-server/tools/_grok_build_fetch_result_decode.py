"""Sidecar decoding helpers for grok_build fetch_result."""

from __future__ import annotations

import os
import time
from typing import Any, Literal

from tools._grok_build_envelope import _envelope_result, _read_only_violation
from tools._grok_build_runner import STDOUT_MAX, RunnerResult


def first_record(records: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    return next((r for r in records if r.get("phase") == phase), {})


def last_record(
    records: list[dict[str, Any]], phase: str
) -> dict[str, Any] | None:
    return next((r for r in reversed(records) if r.get("phase") == phase), None)


def started_metadata(started: dict[str, Any]) -> dict[str, Any]:
    argv = started.get("argv") if isinstance(started.get("argv"), list) else []
    permission_mode = str(
        started.get("permission_mode") or _argv_value(argv, "--permission-mode")
    )
    mode = str(started.get("mode") or _mode_from_permission(permission_mode))
    return {
        "cwd": str(started.get("cwd") or _argv_value(argv, "--cwd")),
        "mode": mode or "read_only",
        "permission_mode": permission_mode,
        "model": str(started.get("model") or _argv_value(argv, "--model") or ""),
        "session_id": started.get("session_id") or _argv_value(argv, "--resume") or None,
        "output_format": str(
            started.get("output_format") or _argv_value(argv, "--output-format")
        ),
        "continue_recent": bool(started.get("continue_recent") or "--continue" in argv),
        "git_status_pre": str(started.get("git_status_pre") or ""),
        "dirty_admission": bool(started.get("dirty_admission")),
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
    rr = RunnerResult(
        status=_terminal_status(exit_record),
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_record.get("exit_code"),
        duration_s=_duration_s(first_record(records, "started"), exit_record),
        sidecar_path=sidecar_path,
        truncated=len(stdout.encode()) > STDOUT_MAX,
        git_status_post=str(exit_record.get("git_status_post") or ""),
        git_diff_stat=str(exit_record.get("git_diff_stat") or ""),
        audit_incomplete=audit_incomplete,
        sidecar_gaps=int(exit_record.get("sidecar_gaps") or 0),
        error=stderr[:200],
        dirty_admission=started_meta["dirty_admission"],
    )
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
    )
    out["metadata"].update(
        format=result_format,
        record_count=len(records),
        output_format=started_meta["output_format"],
        continue_recent=started_meta["continue_recent"],
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
    if permission_mode == "acceptEdits":
        return "edit"
    return "read_only"


def _stdout(records: list[dict[str, Any]]) -> str:
    chunks = [
        str(r.get("data", "")) for r in records if r.get("phase") == "stdout_chunk"
    ]
    if len(chunks) <= 1:
        return "".join(chunks)
    return "\n".join(chunks)


def _stderr(records: list[dict[str, Any]]) -> str:
    return "".join(
        str(r.get("data", "")) for r in records if r.get("phase") == "stderr_chunk"
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
