"""Typed dispatch contracts for the cursorbuild surface.

Two frozen, slotted dataclasses shared across ``cursorbuild.argv``,
``cursorbuild.home``, and the runner/envelope modules that land in later
phases. Mirrors ``grokbuild.runner_types`` retargeted to ``cursor-agent``:
grok-only fields are dropped and cursor-agent worktree/session flags added.

``RunnerSpec`` is the immutable description of one dispatch (what to run and
how); ``RunnerResult`` is the immutable outcome (what happened). Capacity /
truncation constants live with the runner phase, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["read_only", "edit"]
ReadOnlyMode = Literal["plan", "ask"]
Tier = Literal["reasoning", "default", "bulk", "code", "verify"]
ResultStatus = Literal["completed", "failed", "timeout"]


@dataclass(frozen=True, slots=True)
class RunnerSpec:
    """Immutable description of a single cursor-agent dispatch.

    Required fields (no defaults) come first so positional construction in
    tests and callers stays unambiguous. ``model`` / ``system_context`` /
    ``session_id`` / ``timeout_seconds`` are required but nullable: the caller
    must decide them explicitly (passing ``None`` to defer to a downstream
    default) rather than silently inheriting a dataclass default.
    """

    dispatch_id: str
    cwd: str
    prompt: str
    mode: Mode
    cursor_agent_bin: str
    model: str | None
    system_context: str | None
    session_id: str | None
    timeout_seconds: int | None
    tier: Tier
    read_only_mode: ReadOnlyMode = "plan"
    mcp_enabled: bool = False
    force: bool = False
    continue_session: bool = False
    worktree_name: str | None = None
    worktree_base: str | None = None
    skip_worktree_setup: bool = False
    stream_partial_output: bool = False
    recursion_depth: int | None = None


@dataclass(frozen=True, slots=True)
class RunnerResult:
    """Immutable outcome of a single cursor-agent dispatch."""

    status: ResultStatus
    stdout: str
    stderr: str
    exit_code: int | None
    duration_s: float
    sidecar_path: str | None
    truncated: bool
    error: str = ""
    reason_code: str = ""
    resolved_session_id: str | None = None
    tool_call_names: tuple[str, ...] = ()
    usage: dict[str, int] | None = field(default=None)
