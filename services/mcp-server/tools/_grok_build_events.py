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
) -> Event:
    return Event(
        signal="mcp.grok.build.dispatch.called",
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
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
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
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
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
    audit_incomplete: bool = False,
    sidecar_gaps: int = 0,
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
        signal="mcp.grok.build.dispatch.rejected",
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


@event_factory
def GrokBuildCreateCalled(  # noqa: N802
    dispatch_id: str,
    name: str,
    branch: str,
    source_repo: str,
    create_branch: bool = False,
    start_point: str = "",
) -> Event:
    return Event(
        signal="mcp.grok.build.create.called",
        payload={
            "dispatch_id": dispatch_id,
            "name": name,
            "branch": branch,
            "source_repo": source_repo,
            "create_branch": create_branch,
            "start_point": start_point,
        },
        scope="global",
    )


@event_factory
def GrokBuildCreateCompleted(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    exit_code: int,
    name: str,
    branch: str,
    source_repo: str,
    worktree_path: str,
    create_branch: bool = False,
    start_point: str = "",
) -> Event:
    return Event(
        signal="mcp.grok.build.create.completed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "exit_code": exit_code,
            "name": name,
            "branch": branch,
            "source_repo": source_repo,
            "worktree_path": worktree_path,
            "create_branch": create_branch,
            "start_point": start_point,
        },
        scope="global",
    )


@event_factory
def GrokBuildCreateFailed(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    exit_code: int | None,
    error: str,
    name: str,
    branch: str,
    source_repo: str,
    worktree_path: str,
    create_branch: bool = False,
    start_point: str = "",
) -> Event:
    return Event(
        signal="mcp.grok.build.create.failed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "exit_code": exit_code,
            "error": error,
            "name": name,
            "branch": branch,
            "source_repo": source_repo,
            "worktree_path": worktree_path,
            "create_branch": create_branch,
            "start_point": start_point,
        },
        scope="global",
    )


@event_factory
def GrokBuildCreateRejected(  # noqa: N802
    dispatch_id: str,
    reason_code: str,
    reason: str,
    name: str = "",
    branch: str = "",
    source_repo: str = "",
    create_branch: bool = False,
    start_point: str = "",
) -> Event:
    return Event(
        signal="mcp.grok.build.create.rejected",
        payload={
            "dispatch_id": dispatch_id,
            "reason_code": reason_code,
            "reason": reason,
            "name": name,
            "branch": branch,
            "source_repo": source_repo,
            "create_branch": create_branch,
            "start_point": start_point,
        },
        scope="global",
    )


@event_factory
def GrokBuildRemoveCalled(  # noqa: N802
    dispatch_id: str,
    name: str,
) -> Event:
    return Event(
        signal="mcp.grok.build.remove.called",
        payload={"dispatch_id": dispatch_id, "name": name},
        scope="global",
    )


@event_factory
def GrokBuildRemoveCompleted(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    exit_code: int,
    name: str,
    worktree_path: str,
) -> Event:
    return Event(
        signal="mcp.grok.build.remove.completed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "exit_code": exit_code,
            "name": name,
            "worktree_path": worktree_path,
        },
        scope="global",
    )


@event_factory
def GrokBuildRemoveFailed(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    exit_code: int | None,
    error: str,
    name: str,
    worktree_path: str,
) -> Event:
    return Event(
        signal="mcp.grok.build.remove.failed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "exit_code": exit_code,
            "error": error,
            "name": name,
            "worktree_path": worktree_path,
        },
        scope="global",
    )


@event_factory
def GrokBuildRemoveRejected(  # noqa: N802
    dispatch_id: str,
    reason_code: str,
    reason: str,
    name: str = "",
    worktree_path: str = "",
) -> Event:
    return Event(
        signal="mcp.grok.build.remove.rejected",
        payload={
            "dispatch_id": dispatch_id,
            "reason_code": reason_code,
            "reason": reason,
            "name": name,
            "worktree_path": worktree_path,
        },
        scope="global",
    )


def emit_grok_build_create_called(**kwargs: object) -> None:
    _emit(GrokBuildCreateCalled(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_create_completed(**kwargs: object) -> None:
    _emit(GrokBuildCreateCompleted(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_create_failed(**kwargs: object) -> None:
    _emit(GrokBuildCreateFailed(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_create_rejected(**kwargs: object) -> None:
    _emit(GrokBuildCreateRejected(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_remove_called(**kwargs: object) -> None:
    _emit(GrokBuildRemoveCalled(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_remove_completed(**kwargs: object) -> None:
    _emit(GrokBuildRemoveCompleted(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_remove_failed(**kwargs: object) -> None:
    _emit(GrokBuildRemoveFailed(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_remove_rejected(**kwargs: object) -> None:
    _emit(GrokBuildRemoveRejected(**kwargs))  # type: ignore[arg-type]


@event_factory
def GrokBuildListCalled(  # noqa: N802
    dispatch_id: str,
    worktree_root: str,
) -> Event:
    return Event(
        signal="mcp.grok.build.list.called",
        payload={"dispatch_id": dispatch_id, "worktree_root": worktree_root},
        scope="global",
    )


@event_factory
def GrokBuildListCompleted(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    worktree_root: str,
    count: int,
) -> Event:
    return Event(
        signal="mcp.grok.build.list.completed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "worktree_root": worktree_root,
            "count": count,
        },
        scope="global",
    )


@event_factory
def GrokBuildListFailed(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    error: str,
    worktree_root: str,
) -> Event:
    return Event(
        signal="mcp.grok.build.list.failed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "error": error,
            "worktree_root": worktree_root,
        },
        scope="global",
    )


def emit_grok_build_list_called(**kwargs: object) -> None:
    _emit(GrokBuildListCalled(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_list_completed(**kwargs: object) -> None:
    _emit(GrokBuildListCompleted(**kwargs))  # type: ignore[arg-type]


def emit_grok_build_list_failed(**kwargs: object) -> None:
    _emit(GrokBuildListFailed(**kwargs))  # type: ignore[arg-type]


@event_factory
def GrokBuildRegistryRecovered(  # noqa: N802
    entries_recovered: int,
    entries_pruned: int,
    schema_version: int,
) -> Event:
    return Event(
        signal="mcp.grok.build.registry.recovered",
        payload={
            "entries_recovered": entries_recovered,
            "entries_pruned": entries_pruned,
            "schema_version": schema_version,
        },
        scope="global",
    )


def emit_grok_build_registry_recovered(**kwargs: object) -> None:
    _emit(GrokBuildRegistryRecovered(**kwargs))  # type: ignore[arg-type]
