"""Event factories and emitters for grokbuild dispatch lifecycle."""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from grokbuild.events_core import _emit


@event_factory
def GrokBuildDispatchCalled(  # noqa: N802
    dispatch_id: str,
    mode: str,
    op: str,
    session_id: str = "",
    model: str = "",
) -> Event:
    return Event(
        signal="mcp.grokbuild.dispatch.called",
        payload={
            "dispatch_id": dispatch_id,
            "mode": mode,
            "op": op,
            "session_id": session_id,
            "model": model,
        },
        scope="global",
    )


@event_factory
def GrokBuildDispatchCompleted(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    exit_code: int,
    truncated: bool,
    git_status_pre: str,
    git_status_post: str,
    git_diff_stat: str,
    read_only_violation: bool,
    cwd: str = "",
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
) -> Event:
    return Event(
        signal="mcp.grokbuild.dispatch.completed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "exit_code": exit_code,
            "truncated": truncated,
            "cwd": cwd,
            "git_status_pre": git_status_pre,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "read_only_violation": read_only_violation,
            "audit_incomplete": audit_incomplete,
            "sidecar_gaps": sidecar_gaps,
        },
        scope="global",
    )


@event_factory
def GrokBuildDispatchFailed(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    exit_code: int | None,
    error: str,
    git_status_pre: str,
    git_status_post: str,
    git_diff_stat: str,
    read_only_violation: bool,
    cwd: str = "",
    reason_code: str = "",
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
) -> Event:
    return Event(
        signal="mcp.grokbuild.dispatch.failed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "exit_code": exit_code,
            "error": error,
            "reason_code": reason_code,
            "cwd": cwd,
            "git_status_pre": git_status_pre,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "read_only_violation": read_only_violation,
            "audit_incomplete": audit_incomplete,
            "sidecar_gaps": sidecar_gaps,
        },
        scope="global",
    )


@event_factory
def GrokBuildDispatchTimeout(  # noqa: N802
    dispatch_id: str,
    timeout_seconds: int,
    git_status_pre: str,
    git_status_post: str,
    git_diff_stat: str,
    read_only_violation: bool,
    cwd: str = "",
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
) -> Event:
    return Event(
        signal="mcp.grokbuild.dispatch.timeout",
        payload={
            "dispatch_id": dispatch_id,
            "timeout_seconds": timeout_seconds,
            "cwd": cwd,
            "git_status_pre": git_status_pre,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "read_only_violation": read_only_violation,
            "audit_incomplete": audit_incomplete,
            "sidecar_gaps": sidecar_gaps,
        },
        scope="global",
    )


@event_factory
def GrokBuildDispatchRejected(  # noqa: N802
    dispatch_id: str,
    reason_code: str,
    reason: str,
    mode: str = "",
    op: str = "",
    cwd: str = "",
    model: str = "",
) -> Event:
    return Event(
        signal="mcp.grokbuild.dispatch.rejected",
        payload={
            "dispatch_id": dispatch_id,
            "reason_code": reason_code,
            "reason": reason,
            "mode": mode,
            "op": op,
            "cwd": cwd,
            "model": model,
        },
        scope="global",
    )


@event_factory
def GrokBuildDispatchToolCalls(  # noqa: N802
    dispatch_id: str,
    tool_count: int,
    tool_names: list[str],
) -> Event:
    """C.1(ii): summary of MCP tool calls extracted from sidecar streaming-JSON.

    Emitted after every completed/failed dispatch that had stdout to parse,
    even when tool_count == 0. The zero-count case is the raw signal; the
    anomaly event (zero_tool_calls_when_expected) fires on top of it when
    mode='edit' implies tool calls were expected.

    JOIN this event with ``mcp.request.completed`` (seat=grok-build-dispatch,
    dispatch_id) to detect header-vs-sidecar discrepancies.
    """
    return Event(
        signal="mcp.grokbuild.dispatch.tool_calls",
        payload={
            "dispatch_id": dispatch_id,
            "tool_count": tool_count,
            "tool_names": tool_names,
        },
        scope="global",
    )


@event_factory
def GrokBuildDispatchZeroToolCallsWhenExpected(  # noqa: N802
    dispatch_id: str,
    mode: str,
) -> Event:
    """C.1(ii) anomaly: edit-mode dispatch completed with zero sidecar tool calls.

    Fires when tool_count == 0 AND mode == 'edit'. In read_only mode zero
    tool calls is normal (advisory narrative only); in edit mode the grok
    subprocess is expected to call vortex MCP tools to carry out its work.
    Zero calls indicates the dispatch HOME override may have failed silently,
    the grok subprocess ignored MCP, or the task was trivially answerable.
    """
    return Event(
        signal="mcp.grokbuild.dispatch.zero_tool_calls_when_expected",
        payload={
            "dispatch_id": dispatch_id,
            "mode": mode,
        },
        scope="global",
    )


# Wrappers below are typed with the same signatures as their factories
# (review G4) — this eliminates the per-call ``# type: ignore[arg-type]``
# the prior ``**kwargs: object`` form required, and lets call sites get
# proper completion and argument checking from a type checker.


def emit_grok_build_dispatch_called(
    *,
    dispatch_id: str,
    mode: str,
    op: str,
    session_id: str = "",
    model: str = "",
) -> None:
    _emit(
        GrokBuildDispatchCalled(
            dispatch_id=dispatch_id,
            mode=mode,
            op=op,
            session_id=session_id,
            model=model,
        )
    )


def emit_grok_build_dispatch_completed(
    *,
    dispatch_id: str,
    duration_s: float,
    exit_code: int,
    truncated: bool,
    git_status_pre: str,
    git_status_post: str,
    git_diff_stat: str,
    read_only_violation: bool,
    cwd: str = "",
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
) -> None:
    _emit(
        GrokBuildDispatchCompleted(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=exit_code,
            truncated=truncated,
            cwd=cwd,
            git_status_pre=git_status_pre,
            git_status_post=git_status_post,
            git_diff_stat=git_diff_stat,
            read_only_violation=read_only_violation,
            audit_incomplete=audit_incomplete,
            sidecar_gaps=sidecar_gaps,
        )
    )


def emit_grok_build_dispatch_failed(
    *,
    dispatch_id: str,
    duration_s: float,
    exit_code: int | None,
    error: str,
    git_status_pre: str,
    git_status_post: str,
    git_diff_stat: str,
    read_only_violation: bool,
    cwd: str = "",
    reason_code: str = "",
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
) -> None:
    _emit(
        GrokBuildDispatchFailed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=exit_code,
            error=error,
            cwd=cwd,
            reason_code=reason_code,
            git_status_pre=git_status_pre,
            git_status_post=git_status_post,
            git_diff_stat=git_diff_stat,
            read_only_violation=read_only_violation,
            audit_incomplete=audit_incomplete,
            sidecar_gaps=sidecar_gaps,
        )
    )


def emit_grok_build_dispatch_timeout(
    *,
    dispatch_id: str,
    timeout_seconds: int,
    git_status_pre: str,
    git_status_post: str,
    git_diff_stat: str,
    read_only_violation: bool,
    cwd: str = "",
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
) -> None:
    _emit(
        GrokBuildDispatchTimeout(
            dispatch_id=dispatch_id,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
            git_status_pre=git_status_pre,
            git_status_post=git_status_post,
            git_diff_stat=git_diff_stat,
            read_only_violation=read_only_violation,
            audit_incomplete=audit_incomplete,
            sidecar_gaps=sidecar_gaps,
        )
    )


def emit_grok_build_dispatch_rejected(
    *,
    dispatch_id: str,
    reason_code: str,
    reason: str,
    mode: str = "",
    op: str = "",
    cwd: str = "",
    model: str = "",
) -> None:
    _emit(
        GrokBuildDispatchRejected(
            dispatch_id=dispatch_id,
            reason_code=reason_code,
            reason=reason,
            mode=mode,
            op=op,
            cwd=cwd,
            model=model,
        )
    )


def emit_grok_build_dispatch_tool_calls(
    *,
    dispatch_id: str,
    tool_count: int,
    tool_names: list[str],
) -> None:
    _emit(
        GrokBuildDispatchToolCalls(
            dispatch_id=dispatch_id,
            tool_count=tool_count,
            tool_names=tool_names,
        )
    )


def emit_grok_build_dispatch_zero_tool_calls_when_expected(
    *,
    dispatch_id: str,
    mode: str,
) -> None:
    _emit(
        GrokBuildDispatchZeroToolCallsWhenExpected(
            dispatch_id=dispatch_id,
            mode=mode,
        )
    )
