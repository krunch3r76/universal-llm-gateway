"""Sidecar I/O helpers and post-dispatch git-state capture for cursorbuild (Phase 2 passive only).

Mirrors the agnostic kernel from grokbuild.runner_sidecar (append/try/gaps,
chunk-with-truncation, capture_post_state) as cursorbuild-local code. No
grokbuild imports. The three probes are forked to cursor-agent stream-json
shapes (type+subtype discriminator; snake_case session_id; nested
tool_call.mcpToolCall.args.toolName; usage block on result/success).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time

from universal_logging import get_logger

logger = get_logger(__name__)

# Capacity constants (duplicated here for Phase-2 sidecar independence;
# Phase 3 runner will consolidate with runner_types like grokbuild does).
SIDECAR_STDOUT_LINE_MAX: int = 32 * 1024
SIDECAR_STDERR_BYTE_MAX: int = 256 * 1024


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
        logger.debug("cursorbuild.sidecar: append gap on %s (gaps=%d)", path, gaps[0])


def snap_session_id(line: bytes | str) -> str | None:
    """Best-effort parse: return ``session_id`` (snake_case) from a single line.

    Only returns a value for lines where type=="system" AND subtype=="init".
    Returns None if the line is not JSON, not a dict, or fails the gate.
    Never raises.
    """
    try:
        rec = json.loads(line)
    except (ValueError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(rec, dict):
        return None
    if rec.get("type") != "system" or rec.get("subtype") != "init":
        return None
    sid = rec.get("session_id")
    return sid if isinstance(sid, str) and sid else None


def parse_tool_calls(stdout_bytes: bytes) -> list[dict[str, str]]:
    """Parse streaming-JSON lines and return tool call records with lifecycle.

    Matches type=="tool_call". Extracts bare name from the nested path
    tool_call.mcpToolCall.args.toolName (not the prefixed args.name).
    Captures subtype ("started" | "completed") for lifecycle semantics.
    Returns list of {"toolName": <bare>, "subtype": <str>} preserving order.

    Never raises — best-effort. Malformed JSON, bad nesting, or missing
    fields are skipped so a dispatch cannot be blocked by parse errors.
    Rejected tool results (result.rejected present under mcpToolCall) are
    tolerated without raising; name is still extracted when present.
    """
    tool_records: list[dict[str, str]] = []
    for raw in stdout_bytes.splitlines():
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") != "tool_call":
            continue
        tc = rec.get("tool_call")
        if not isinstance(tc, dict):
            continue
        mcp = tc.get("mcpToolCall")
        if not isinstance(mcp, dict):
            continue
        args = mcp.get("args") or {}
        if not isinstance(args, dict):
            continue
        name = args.get("toolName")
        if isinstance(name, str) and name:
            subtype = rec.get("subtype")
            subtype_str = subtype if isinstance(subtype, str) else ""
            tool_records.append({"toolName": name, "subtype": subtype_str})
    return tool_records


def extract_usage(line: bytes | str) -> dict[str, int] | None:
    """Best-effort extract of usage token accounting from a result line.

    Returns a dict containing the four token fields (camelCase keys as
    emitted) when the line has type=="result" AND subtype=="success".
    is_error==true (or absent) is tolerated without raising; usage may be
    present or absent on such lines. Missing fields or no usage block
    yields None (or a partial dict with 0 for absent keys).
    """
    try:
        rec = json.loads(line)
    except (ValueError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(rec, dict):
        return None
    if rec.get("type") != "result" or rec.get("subtype") != "success":
        return None
    usage = rec.get("usage")
    if not isinstance(usage, dict):
        return None
    keys = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
    out: dict[str, int] = {}
    for k in keys:
        v = usage.get(k)
        if isinstance(v, int | float):
            out[k] = int(v)
        elif isinstance(v, str):
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = 0
        else:
            out[k] = 0
    return out if out else None


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
