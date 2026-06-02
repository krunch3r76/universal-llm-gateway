"""Async subprocess runner for cursor-agent dispatches."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Literal

from universal_logging import get_logger

from cursorbuild.argv import build_argv, _build_env
from cursorbuild.constants import _SIDECAR_DIR
from cursorbuild.home import CursorbuildConfigError, setup_dispatch_home
from cursorbuild.registry import record_pid
from cursorbuild.runner_types import RunnerResult, RunnerSpec
from cursorbuild import sidecar

logger = get_logger(__name__)

STDOUT_MAX = 64 * 1024
STDERR_MAX = 16 * 1024


def _truncate_tail(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    if truncated:
        data = data[-limit:]
    return data.decode(errors="replace"), truncated


def _sidecar_path(dispatch_id: str) -> str:
    return str(_SIDECAR_DIR / f"{dispatch_id}.ndjson")


async def run_dispatch(spec: RunnerSpec) -> RunnerResult:
    t0 = time.monotonic()
    argv = build_argv(spec)
    sidecar_path = _sidecar_path(spec.dispatch_id)
    gaps: list[int] = [0]

    _env = _build_env()
    if spec.recursion_depth is not None:
        _env["CURSORBUILD_RECURSION_DEPTH"] = str(spec.recursion_depth)

    _real_home = os.environ.get("HOME", "")
    try:
        _dispatch_home = setup_dispatch_home(
            spec.dispatch_id,
            _SIDECAR_DIR,
            real_home=_real_home,
            mcp_enabled=spec.mcp_enabled,
        )
    except (OSError, CursorbuildConfigError) as exc:
        return RunnerResult(
            status="failed",
            stdout="",
            stderr="",
            exit_code=None,
            duration_s=time.monotonic() - t0,
            sidecar_path=None,
            truncated=False,
            audit_incomplete=True,
            error=f"dispatch_home_setup_failed: {exc}",
            reason_code="dispatch_home_setup_failed",
        )
    _env["HOME"] = str(_dispatch_home)

    try:
        sidecar._append_sidecar(
            sidecar_path,
            {
                "phase": "started",
                "ts": int(time.time() * 1000),
                "argv": argv,
                "env_keys": sorted(_env),
                "cwd": spec.cwd,
                "mode": spec.mode,
                "read_only_mode": spec.read_only_mode,
                "model": spec.model,
                "session_id": spec.session_id,
                "tier": spec.tier,
                "output_format": "stream-json",
                "git_status_pre": spec.git_status_pre,
                "dirty_admission": spec.dirty_admission,
                "mcp_enabled": spec.mcp_enabled,
            },
        )
    except OSError as exc:
        return RunnerResult(
            status="failed",
            stdout="",
            stderr="",
            exit_code=None,
            duration_s=time.monotonic() - t0,
            sidecar_path=None,
            truncated=False,
            audit_incomplete=True,
            error=f"sidecar_write_failed: {exc}",
            reason_code="sidecar_unwritable",
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=spec.cwd,
            env=_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        if spec.proc_pid_holder is not None:
            spec.proc_pid_holder.append(-1)
        duration_s = time.monotonic() - t0
        sidecar._try_append_sidecar(
            sidecar_path,
            {
                "phase": "exit",
                "ts": int(time.time() * 1000),
                "status": "failed",
                "exit_code": None,
                "duration_s": duration_s,
                "error": f"spawn_failed: {exc}",
                "reason_code": "spawn_failed",
            },
            gaps,
        )
        return RunnerResult(
            status="failed",
            stdout="",
            stderr="",
            exit_code=None,
            duration_s=duration_s,
            sidecar_path=sidecar_path,
            truncated=False,
            audit_incomplete=True,
            sidecar_gaps=gaps[0],
            error=f"spawn_failed: {exc}",
            reason_code="spawn_failed",
        )

    if spec.proc_pid_holder is not None:
        spec.proc_pid_holder.append(proc.pid)
    await record_pid(spec.cwd, spec.dispatch_id, proc.pid)

    try:
        if spec.timeout_seconds is None:
            stdout_b, stderr_b = await proc.communicate()
        else:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=spec.timeout_seconds,
            )
    except TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (ProcessLookupError, TimeoutError):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        (
            git_status_post,
            git_diff_stat,
            audit_incomplete,
        ) = await sidecar._capture_post_state(spec.cwd)
        duration_s = time.monotonic() - t0
        sidecar._try_append_sidecar(
            sidecar_path,
            {
                "phase": "exit",
                "ts": int(time.time() * 1000),
                "status": "timeout",
                "exit_code": None,
                "duration_s": duration_s,
                "git_status_post": git_status_post,
                "git_diff_stat": git_diff_stat,
                "audit_incomplete": audit_incomplete,
                "sidecar_gaps": gaps[0],
            },
            gaps,
        )
        return RunnerResult(
            status="timeout",
            stdout="",
            stderr="",
            exit_code=None,
            duration_s=duration_s,
            sidecar_path=sidecar_path,
            truncated=False,
            git_status_post=git_status_post,
            git_diff_stat=git_diff_stat,
            audit_incomplete=audit_incomplete,
            sidecar_gaps=gaps[0],
        )

    resolved_session_id: str | None = None
    usage: dict[str, int] | None = None
    for line in stdout_b.splitlines():
        if resolved_session_id is None:
            resolved_session_id = sidecar.snap_session_id(line)
        if usage is None:
            usage = sidecar.extract_usage(line)
        sidecar._try_append_sidecar_chunk(
            sidecar_path,
            phase="stdout_chunk",
            data=line.decode(errors="replace"),
            cap=sidecar.SIDECAR_STDOUT_LINE_MAX,
            gaps=gaps,
        )
    tool_records = sidecar.parse_tool_calls(stdout_b)
    tool_names = tuple(r["toolName"] for r in tool_records if r.get("toolName"))

    if stderr_b:
        sidecar._try_append_sidecar_chunk(
            sidecar_path,
            phase="stderr_chunk",
            data=stderr_b.decode(errors="replace"),
            cap=sidecar.SIDECAR_STDERR_BYTE_MAX,
            gaps=gaps,
        )

    (
        git_status_post,
        git_diff_stat,
        audit_incomplete,
    ) = await sidecar._capture_post_state(spec.cwd)
    exit_code = proc.returncode
    duration_s = time.monotonic() - t0
    status: Literal["completed", "failed"] = "completed" if exit_code == 0 else "failed"
    reason_code = "" if status == "completed" else "cursor_agent_nonzero_exit"
    stdout, truncated = _truncate_tail(stdout_b, STDOUT_MAX)
    stderr, _ = _truncate_tail(stderr_b, STDERR_MAX)

    sidecar._try_append_sidecar(
        sidecar_path,
        {
            "phase": "exit",
            "ts": int(time.time() * 1000),
            "status": status,
            "exit_code": exit_code,
            "duration_s": duration_s,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "audit_incomplete": audit_incomplete,
            "sidecar_gaps": gaps[0],
            "resolved_session_id": resolved_session_id,
            "reason_code": reason_code,
            "truncated": truncated,
        },
        gaps,
    )

    return RunnerResult(
        status=status,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_s=duration_s,
        sidecar_path=sidecar_path,
        truncated=truncated,
        git_status_post=git_status_post,
        git_diff_stat=git_diff_stat,
        audit_incomplete=audit_incomplete,
        sidecar_gaps=gaps[0],
        reason_code=reason_code,
        resolved_session_id=resolved_session_id,
        tool_call_names=tool_names,
        usage=usage,
    )
