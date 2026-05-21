"""Async subprocess runner for grokbuild dispatch (V1).

Path classification and reason_code values are documented on
``run_dispatch``. The runner sees fully-resolved scalars only — tier
overlays and mode-aware defaults are applied by ``_grokbuild_dispatch``
before this module is invoked.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

from tools._grokbuild_constants import _NON_REASONING_MODELS, _SIDECAR_DIR

STDOUT_MAX = 64 * 1024  # max bytes of stdout retained in the envelope (post-decode)
STDERR_MAX = 16 * 1024  # max bytes of stderr retained in the envelope (post-decode)
SIDECAR_STDOUT_LINE_MAX = (
    32 * 1024
)  # max CHARACTERS per stdout line persisted to sidecar
SIDECAR_STDERR_BYTE_MAX = 256 * 1024  # max CHARACTERS for stderr persisted to sidecar
# Note: SIDECAR_*_MAX are applied to str via len() so they cap characters,
# not bytes (review W2). For ASCII content chars==bytes; multi-byte UTF-8
# (CJK, emoji, accented Latin) can write 2-4× this size to disk. The
# constant names retain their existing form to avoid breaking the test
# import `from tools._grokbuild_runner import SIDECAR_STDOUT_LINE_MAX`.
# _SIDECAR_DIR is canonically defined in _grokbuild_constants (review W8);
# the re-export above preserves `tools._grokbuild_runner._SIDECAR_DIR`
# as a monkey-patch target for existing test fixtures.

_READ_ONLY_PREFIX = (
    "MODE: read_only. The operator has invoked you in advisory mode. "
    "Do NOT modify, create, or delete source code files. Do NOT run shell commands "
    "that mutate source files (no editor saves, no code generation that writes outputs). "
    "Git index operations (git stash, git stash pop, git stash apply, git add, "
    "git commit, git merge) ARE permitted when they are the explicit stated purpose "
    "of the dispatch — these are bookkeeping operations, not source edits. "
    "For all other tasks: narrate the changes you would propose — describe the diff "
    "in prose, name the files you would touch, and quote the exact patch hunks. "
    "The operator will review your proposal and re-invoke you in edit mode if they "
    "want the changes applied."
)

_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "CORTEX_DB_PATH", "TODOS_DB_PATH")


@dataclass(frozen=True, slots=True)
class RunnerSpec:
    """Frozen execution descriptor consumed by ``run_dispatch``.

    Every field is a fully-resolved scalar — tier overlays, mode-aware
    defaults, and explicit overrides have all been applied by the
    dispatcher before this dataclass is built. Runner code MUST NOT
    re-resolve anything from this struct.
    """

    dispatch_id: str
    cwd: str
    prompt: str
    mode: Literal["read_only", "edit"]
    permission_mode: str
    system_context: str | None
    model: str | None
    session_id: str | None
    timeout_seconds: int
    grok_path: str
    git_status_pre: str

    # V1 param surface (all resolved post-tier-overlay; None means "do not
    # emit the corresponding grok CLI flag").
    tier: Literal["quick", "balanced", "thorough", "max"]
    reasoning_effort: str | None
    effort: str | None
    check: bool
    no_subagents: bool
    disable_web_search: bool
    max_turns: int | None
    best_of_n: int | None
    resume_strict: bool

    dirty_admission: bool = False


@dataclass(frozen=True, slots=True)
class RunnerResult:
    status: Literal["completed", "failed", "timeout"]
    stdout: str
    stderr: str
    exit_code: int | None
    duration_s: float
    sidecar_path: str | None
    truncated: bool
    git_status_post: str
    git_diff_stat: str
    audit_incomplete: bool = False
    sidecar_gaps: int = 0
    error: str = ""
    dirty_admission: bool = False
    # V1 additions:
    reason_code: str = (
        ""  # structured failure category on failed status; "" on success/timeout
    )
    resolved_session_id: str | None = (
        None  # captured from streaming-json stdout sessionId
    )


def _build_env() -> dict[str, str]:
    src = os.environ
    env = {k: src[k] for k in _ALLOW if k in src}
    env["TERM"] = "dumb"
    return env


def _build_argv(spec: RunnerSpec) -> list[str]:
    """Compose the full grok CLI invocation for ``spec``.

    Flag emission rules:

    * ``--output-format`` is always ``streaming-json`` (V1 invariant).
    * Resume: ``-r SESSION`` when ``resume_strict=True``; ``-s SESSION``
      when ``resume_strict=False`` and ``session_id`` is set. ``-s`` is
      idempotent — grok creates a new session if SESSION is unknown.
    * ``--reasoning-effort`` / ``--effort`` / ``--max-turns`` /
      ``--best-of-n``: omitted entirely when the corresponding field is
      ``None`` (caller opt-out). The dispatcher's tier resolver ensures
      ``reasoning_effort`` and ``effort`` are non-None for normal use;
      Plain ``None`` here means "do not pass the flag" — used by tests
      and explicit-skip callers.
    * Boolean flags (``check``, ``no_subagents``, ``disable_web_search``)
      emit the named CLI flag iff True.
    """
    read_only_rules = _READ_ONLY_PREFIX if spec.mode == "read_only" else ""
    combined_rules = "\n\n".join(
        part for part in (read_only_rules, spec.system_context or "") if part
    )
    argv = [
        spec.grok_path,
        "-p",
        spec.prompt,
        "--cwd",
        spec.cwd,
        "--output-format",
        "streaming-json",
        "--permission-mode",
        spec.permission_mode,
        "--always-approve",
    ]
    if spec.model:
        argv.extend(["--model", spec.model])
    if combined_rules:
        argv.extend(["--rules", combined_rules])

    # Session resume — strict (-r) vs idempotent (-s).
    if spec.session_id:
        argv.append("-r" if spec.resume_strict else "-s")
        argv.append(spec.session_id)

    _reasoning_capable = (
        spec.model is not None and spec.model not in _NON_REASONING_MODELS
    )
    if spec.reasoning_effort is not None and _reasoning_capable:
        argv.extend(["--reasoning-effort", spec.reasoning_effort])
    if spec.effort is not None and _reasoning_capable:
        argv.extend(["--effort", spec.effort])
    if spec.max_turns is not None:
        argv.extend(["--max-turns", str(spec.max_turns)])
    if spec.best_of_n is not None:
        argv.extend(["--best-of-n", str(spec.best_of_n)])
    if spec.check:
        argv.append("--check")
    if spec.no_subagents:
        argv.append("--no-subagents")
    if spec.disable_web_search:
        argv.append("--disable-web-search")

    return argv


def _truncate_tail(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    if truncated:
        data = data[-limit:]
    return data.decode(errors="replace"), truncated


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


def _sidecar_path(dispatch_id: str) -> str:
    return str(_SIDECAR_DIR / f"{dispatch_id}.ndjson")


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


async def run_dispatch(spec: RunnerSpec) -> RunnerResult:
    """Spawn grok, capture output, sidecar, and post-invocation git state.

    Path classification:
        * sidecar_write_failed → status='failed', reason_code='sidecar_unwritable'
        * subprocess spawn fails (OSError, FileNotFoundError, PermissionError)
          → status='failed', reason_code='spawn_failed'; NO process to wait on
        * grok exits cleanly with non-zero → status='failed', reason_code='grok_nonzero_exit'
        * timeout fires → status='timeout' (no reason_code)
        * grok exits 0 → status='completed'
    """
    t0 = time.monotonic()
    argv = _build_argv(spec)
    sidecar = _sidecar_path(spec.dispatch_id)
    gaps: list[int] = [0]

    try:
        _append_sidecar(
            sidecar,
            {
                "phase": "started",
                "ts": int(time.time() * 1000),
                "argv": argv,
                "env_keys": sorted(_build_env()),
                "cwd": spec.cwd,
                "mode": spec.mode,
                "permission_mode": spec.permission_mode,
                "model": spec.model,
                "session_id": spec.session_id,
                "resume_strict": spec.resume_strict,
                "tier": spec.tier,
                "reasoning_effort": spec.reasoning_effort,
                "effort": spec.effort,
                "check": spec.check,
                "no_subagents": spec.no_subagents,
                "disable_web_search": spec.disable_web_search,
                "max_turns": spec.max_turns,
                "best_of_n": spec.best_of_n,
                "output_format": "streaming-json",
                "git_status_pre": spec.git_status_pre,
                "dirty_admission": spec.dirty_admission,
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
            git_status_post="",
            git_diff_stat="",
            audit_incomplete=True,
            sidecar_gaps=0,
            error=f"sidecar_write_failed: {exc}",
            reason_code="sidecar_unwritable",
            dirty_admission=spec.dirty_admission,
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=spec.cwd,
            env=_build_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        # FileNotFoundError and PermissionError are subclasses of OSError — the
        # parent catches them too. Naming kept descriptive in the error string.
        duration_s = time.monotonic() - t0
        _try_append_sidecar(
            sidecar,
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
            sidecar_path=sidecar,
            truncated=False,
            git_status_post="",
            git_diff_stat="",
            audit_incomplete=True,
            sidecar_gaps=gaps[0],
            error=f"spawn_failed: {exc}",
            reason_code="spawn_failed",
            dirty_admission=spec.dirty_admission,
        )

    try:
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
        git_status_post, git_diff_stat, audit_incomplete = await _capture_post_state(
            spec.cwd
        )
        duration_s = time.monotonic() - t0
        _try_append_sidecar(
            sidecar,
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
            sidecar_path=sidecar,
            truncated=False,
            git_status_post=git_status_post,
            git_diff_stat=git_diff_stat,
            audit_incomplete=audit_incomplete,
            sidecar_gaps=gaps[0],
            dirty_admission=spec.dirty_admission,
        )
    except OSError as exc:
        # Broken pipe, EBADF, or other I/O failure mid-communicate (review W4).
        # Honor the structured failure envelope contract instead of letting the
        # exception propagate to the MCP error path.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        git_status_post, git_diff_stat, audit_incomplete = await _capture_post_state(
            spec.cwd
        )
        duration_s = time.monotonic() - t0
        _try_append_sidecar(
            sidecar,
            {
                "phase": "exit",
                "ts": int(time.time() * 1000),
                "status": "failed",
                "exit_code": proc.returncode,
                "duration_s": duration_s,
                "git_status_post": git_status_post,
                "git_diff_stat": git_diff_stat,
                "audit_incomplete": audit_incomplete,
                "sidecar_gaps": gaps[0],
                "error": f"communicate_failed: {exc}",
                "reason_code": "communicate_failed",
            },
            gaps,
        )
        return RunnerResult(
            status="failed",
            stdout="",
            stderr="",
            exit_code=proc.returncode,
            duration_s=duration_s,
            sidecar_path=sidecar,
            truncated=False,
            git_status_post=git_status_post,
            git_diff_stat=git_diff_stat,
            audit_incomplete=audit_incomplete,
            sidecar_gaps=gaps[0],
            error=f"communicate_failed: {exc}",
            reason_code="communicate_failed",
            dirty_admission=spec.dirty_admission,
        )

    # streaming-json: split per line, snap first sessionId, sidecar-cap each chunk.
    resolved_session_id: str | None = None
    for line in stdout_b.splitlines():
        if resolved_session_id is None:
            resolved_session_id = _snap_session_id(line)
        _try_append_sidecar_chunk(
            sidecar,
            phase="stdout_chunk",
            data=line.decode(errors="replace"),
            cap=SIDECAR_STDOUT_LINE_MAX,
            gaps=gaps,
        )

    if stderr_b:
        _try_append_sidecar_chunk(
            sidecar,
            phase="stderr_chunk",
            data=stderr_b.decode(errors="replace"),
            cap=SIDECAR_STDERR_BYTE_MAX,
            gaps=gaps,
        )

    git_status_post, git_diff_stat, audit_incomplete = await _capture_post_state(
        spec.cwd
    )
    exit_code = proc.returncode
    duration_s = time.monotonic() - t0
    status: Literal["completed", "failed"] = "completed" if exit_code == 0 else "failed"
    reason_code = "" if status == "completed" else "grok_nonzero_exit"

    # Compute truncation BEFORE the exit-record write so it can be persisted
    # to the sidecar (review C2). Without persistence, the fetch_result decode
    # path has to recompute from reconstructed (possibly truncated) chunks,
    # which can flip True→False across decode.
    stdout, truncated = _truncate_tail(stdout_b, STDOUT_MAX)
    stderr, _ = _truncate_tail(stderr_b, STDERR_MAX)

    _try_append_sidecar(
        sidecar,
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
        sidecar_path=sidecar,
        truncated=truncated,
        git_status_post=git_status_post,
        git_diff_stat=git_diff_stat,
        audit_incomplete=audit_incomplete,
        sidecar_gaps=gaps[0],
        reason_code=reason_code,
        resolved_session_id=resolved_session_id,
        dirty_admission=spec.dirty_admission,
    )
