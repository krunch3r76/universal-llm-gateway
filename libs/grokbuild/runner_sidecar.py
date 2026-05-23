"""Sidecar I/O helpers and post-dispatch git-state capture for the grokbuild runner."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time


def _append_sidecar(path: str, record: dict[str, object]) -> None:
    line = json.dumps(record, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def _try_append_sidecar(path: str, record: dict[str, object], gaps: list[int]) -> None:
    """Append to sidecar; on OSError, increment the shared gaps counter.

    The counter is propagated to the terminal RunnerResult so audit consumers
    can detect partial sidecars (vs silently swallowing OSError).
    """
    try:
        _append_sidecar(path, record)
    except OSError:
        gaps[0] += 1


def parse_tool_calls(stdout_bytes: bytes) -> list[str]:
    """Parse streaming-JSON lines from grok stdout and return tool names called.

    Scans each newline-delimited JSON record for ``type == "tool_use"`` and
    extracts the tool name. Returns names in call order with duplicates
    preserved (counts are meaningful for anomaly detection).

    Never raises — best-effort parse. Unrecognised or malformed lines are
    silently skipped so parse failures cannot block a completed dispatch.

    Grok streaming-JSON format: each line is a JSON object. Tool-use
    records carry ``type="tool_use"`` and ``name="<tool_name>"``.
    """
    tool_names: list[str] = []
    for raw in stdout_bytes.splitlines():
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") != "tool_use":
            continue
        name = rec.get("name") or rec.get("toolName")
        if isinstance(name, str) and name:
            tool_names.append(name)
    return tool_names


def _snap_session_id(line: bytes) -> str | None:
    """Best-effort parse: return ``sessionId`` from a single streaming-JSON line.

    Returns ``None`` if the line is not JSON, not a dict, or has no
    ``sessionId`` field. Never raises.
    """
    try:
        rec = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(rec, dict):
        return None
    sid = rec.get("sessionId")
    return sid if isinstance(sid, str) and sid else None


def _try_append_sidecar_chunk(
    path: str,
    *,
    phase: str,
    data: str,
    cap: int,
    gaps: list[int],
) -> None:
    """Persist a stdout/stderr chunk to the sidecar; record truncation explicitly.

    When ``len(data) > cap``, the persisted record is ``phase + "_truncated"``
    with ``len`` (original) and ``kept`` (capped) so audit consumers can see
    the loss without silent drops. Other OSErrors still increment ``gaps``.
    """
    if len(data) > cap:
        _try_append_sidecar(
            path,
            {
                "phase": f"{phase}_truncated",
                "ts": int(time.time() * 1000),
                "len": len(data),
                "kept": cap,
                "data": data[:cap],
            },
            gaps,
        )
        return
    _try_append_sidecar(
        path,
        {
            "phase": phase,
            "ts": int(time.time() * 1000),
            "data": data,
        },
        gaps,
    )


async def _capture_post_state(cwd: str) -> tuple[str, str, bool]:
    """Capture post-dispatch git state.

    Returns (status_porcelain, diff_stat, audit_incomplete). audit_incomplete
    is True when a git invocation failed (timeout, non-zero exit, OS error) —
    callers MUST treat a True flag as "do not trust the verdict for this
    dispatch", distinct from a clean repo (status="") which is a TRUE clean
    signal.
    """
    loop = asyncio.get_running_loop()

    def _do_capture() -> tuple[str, str, bool]:
        try:
            status_proc = subprocess.run(
                ["git", "-C", cwd, "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return "", "", True
        status = status_proc.stdout
        diff = ""
        if status.strip():
            try:
                diff_proc = subprocess.run(
                    ["git", "-C", cwd, "diff", "--stat"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                # status read succeeded; diff failed — treat verdict as suspect.
                return status, "", True
            diff = diff_proc.stdout
        return status, diff, False

    return await loop.run_in_executor(None, _do_capture)
