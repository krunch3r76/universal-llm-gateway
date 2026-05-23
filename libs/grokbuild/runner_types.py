"""Dataclasses and capacity constants for the grokbuild async runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
# import `from grokbuild.runner import SIDECAR_STDOUT_LINE_MAX`.


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
    # Phase B (V2): when supplied, ``run_dispatch`` appends the spawned
    # subprocess pid to this list immediately after ``create_subprocess_exec``.
    # Used by ``GrokbuildExecutionTracker`` to enable cancellation
    # (SIGTERM → wait 30s → SIGKILL) without lib-internal callback machinery.
    # ``list[int]`` is mutable and survives ``frozen=True`` (we mutate the
    # referenced list, not the field binding).
    proc_pid_holder: list[int] | None = None
    # MQ3: caller-provided dispatch depth for recursion enforcement (G7).
    # When non-None, the runner injects GROKBUILD_RECURSION_DEPTH=<value>
    # into the subprocess env so nested dispatches can propagate the chain.
    recursion_depth: int | None = None


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
    # C.1(ii): tool names extracted from streaming-json stdout (phase C observability).
    # Populated by runner after successful communicate(); empty on timeout/spawn-failed.
    # Ordered by call time, duplicates preserved so count == len(tool_call_names).
    tool_call_names: tuple[str, ...] = ()
