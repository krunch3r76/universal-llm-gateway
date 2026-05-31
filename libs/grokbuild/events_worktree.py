"""Event factories and emitters for grokbuild worktree remove/list/registry."""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from grokbuild.events_core import _emit


@event_factory
def GrokBuildRemoveCalled(  # noqa: N802
    dispatch_id: str,
    name: str,
) -> Event:
    return Event(
        signal="mcp.grokbuild.remove.called",
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
        signal="mcp.grokbuild.remove.completed",
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
        signal="mcp.grokbuild.remove.failed",
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
        signal="mcp.grokbuild.remove.rejected",
        payload={
            "dispatch_id": dispatch_id,
            "reason_code": reason_code,
            "reason": reason,
            "name": name,
            "worktree_path": worktree_path,
        },
        scope="global",
    )


def emit_grok_build_remove_called(*, dispatch_id: str, name: str) -> None:
    _emit(GrokBuildRemoveCalled(dispatch_id=dispatch_id, name=name))


def emit_grok_build_remove_completed(
    *,
    dispatch_id: str,
    duration_s: float,
    exit_code: int,
    name: str,
    worktree_path: str,
) -> None:
    _emit(
        GrokBuildRemoveCompleted(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=exit_code,
            name=name,
            worktree_path=worktree_path,
        )
    )


def emit_grok_build_remove_failed(
    *,
    dispatch_id: str,
    duration_s: float,
    exit_code: int | None,
    error: str,
    name: str,
    worktree_path: str,
) -> None:
    _emit(
        GrokBuildRemoveFailed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=exit_code,
            error=error,
            name=name,
            worktree_path=worktree_path,
        )
    )


def emit_grok_build_remove_rejected(
    *,
    dispatch_id: str,
    reason_code: str,
    reason: str,
    name: str = "",
    worktree_path: str = "",
) -> None:
    _emit(
        GrokBuildRemoveRejected(
            dispatch_id=dispatch_id,
            reason_code=reason_code,
            reason=reason,
            name=name,
            worktree_path=worktree_path,
        )
    )


@event_factory
def GrokBuildListCalled(  # noqa: N802
    dispatch_id: str,
    worktree_root: str,
) -> Event:
    return Event(
        signal="mcp.grokbuild.list.called",
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
        signal="mcp.grokbuild.list.completed",
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
        signal="mcp.grokbuild.list.failed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "error": error,
            "worktree_root": worktree_root,
        },
        scope="global",
    )


def emit_grok_build_list_called(*, dispatch_id: str, worktree_root: str) -> None:
    _emit(GrokBuildListCalled(dispatch_id=dispatch_id, worktree_root=worktree_root))


def emit_grok_build_list_completed(
    *,
    dispatch_id: str,
    duration_s: float,
    worktree_root: str,
    count: int,
) -> None:
    _emit(
        GrokBuildListCompleted(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            worktree_root=worktree_root,
            count=count,
        )
    )


def emit_grok_build_list_failed(
    *,
    dispatch_id: str,
    duration_s: float,
    error: str,
    worktree_root: str,
) -> None:
    _emit(
        GrokBuildListFailed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            error=error,
            worktree_root=worktree_root,
        )
    )


@event_factory
def GrokBuildRegistryRecovered(  # noqa: N802
    entries_recovered: int,
    entries_pruned: int,
    schema_version: int,
) -> Event:
    return Event(
        signal="mcp.grokbuild.registry.recovered",
        payload={
            "entries_recovered": entries_recovered,
            "entries_pruned": entries_pruned,
            "schema_version": schema_version,
        },
        scope="global",
    )


def emit_grok_build_registry_recovered(
    *,
    entries_recovered: int,
    entries_pruned: int,
    schema_version: int,
) -> None:
    _emit(
        GrokBuildRegistryRecovered(
            entries_recovered=entries_recovered,
            entries_pruned=entries_pruned,
            schema_version=schema_version,
        )
    )


@event_factory
def GrokBuildLockReaped(  # noqa: N802
    cwd: str,
    holders_reaped: int,
) -> Event:
    return Event(
        signal="mcp.grokbuild.lock.reaped",
        payload={"cwd": cwd, "holders_reaped": holders_reaped},
        scope="global",
    )


def emit_grok_build_lock_reaped(*, cwd: str, holders_reaped: int) -> None:
    _emit(GrokBuildLockReaped(cwd=cwd, holders_reaped=holders_reaped))
