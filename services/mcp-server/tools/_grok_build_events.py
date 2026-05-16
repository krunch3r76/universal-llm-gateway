"""Event factories for grok_build dispatch lifecycle."""

from __future__ import annotations

from mcp_events import record
from universal_event_bus import Event, event_factory


@event_factory
def GrokBuildDispatchCalled(  # noqa: N802
    dispatch_id: str,
    mode: str,
    op: str,
    session_id: str = "",
    model: str = "",
    git_status_pre: str = "",
) -> Event:
    return Event(
        signal="mcp.grok.build.dispatch.called",
        payload={
            "dispatch_id": dispatch_id,
            "mode": mode,
            "op": op,
            "session_id": session_id,
            "model": model,
            "git_status_pre": git_status_pre,
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
) -> Event:
    return Event(
        signal="mcp.grok.build.dispatch.completed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "exit_code": exit_code,
            "truncated": truncated,
            "git_status_pre": git_status_pre,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "read_only_violation": read_only_violation,
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
) -> Event:
    return Event(
        signal="mcp.grok.build.dispatch.failed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "exit_code": exit_code,
            "error": error,
            "git_status_pre": git_status_pre,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "read_only_violation": read_only_violation,
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
) -> Event:
    return Event(
        signal="mcp.grok.build.dispatch.timeout",
        payload={
            "dispatch_id": dispatch_id,
            "timeout_seconds": timeout_seconds,
            "git_status_pre": git_status_pre,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "read_only_violation": read_only_violation,
        },
        scope="global",
    )


@event_factory
def GrokBuildDispatchRejected(  # noqa: N802
    dispatch_id: str,
    reason_code: str,
    reason: str,
) -> Event:
    return Event(
        signal="mcp.grok.build.dispatch.rejected",
        payload={
            "dispatch_id": dispatch_id,
            "reason_code": reason_code,
            "reason": reason,
        },
        scope="global",
    )


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)


def emit_grok_build_dispatch_called(**kwargs: object) -> None:
    _emit(GrokBuildDispatchCalled(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_dispatch_completed(**kwargs: object) -> None:
    _emit(GrokBuildDispatchCompleted(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_dispatch_failed(**kwargs: object) -> None:
    _emit(GrokBuildDispatchFailed(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_dispatch_timeout(**kwargs: object) -> None:
    _emit(GrokBuildDispatchTimeout(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_dispatch_rejected(**kwargs: object) -> None:
    _emit(GrokBuildDispatchRejected(**kwargs))  # type: ignore[arg-type]
