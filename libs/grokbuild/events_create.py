"""Event factories and emitters for grokbuild worktree-create lifecycle."""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from grokbuild.events_core import _emit


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
        signal="mcp.grokbuild.create.called",
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
        signal="mcp.grokbuild.create.completed",
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
        signal="mcp.grokbuild.create.failed",
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
        signal="mcp.grokbuild.create.rejected",
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


def emit_grok_build_create_called(
    *,
    dispatch_id: str,
    name: str,
    branch: str,
    source_repo: str,
    create_branch: bool = False,
    start_point: str = "",
) -> None:
    _emit(
        GrokBuildCreateCalled(
            dispatch_id=dispatch_id,
            name=name,
            branch=branch,
            source_repo=source_repo,
            create_branch=create_branch,
            start_point=start_point,
        )
    )


def emit_grok_build_create_completed(
    *,
    dispatch_id: str,
    duration_s: float,
    exit_code: int,
    name: str,
    branch: str,
    source_repo: str,
    worktree_path: str,
    create_branch: bool = False,
    start_point: str = "",
) -> None:
    _emit(
        GrokBuildCreateCompleted(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=exit_code,
            name=name,
            branch=branch,
            source_repo=source_repo,
            worktree_path=worktree_path,
            create_branch=create_branch,
            start_point=start_point,
        )
    )


def emit_grok_build_create_failed(
    *,
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
) -> None:
    _emit(
        GrokBuildCreateFailed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=exit_code,
            error=error,
            name=name,
            branch=branch,
            source_repo=source_repo,
            worktree_path=worktree_path,
            create_branch=create_branch,
            start_point=start_point,
        )
    )


def emit_grok_build_create_rejected(
    *,
    dispatch_id: str,
    reason_code: str,
    reason: str,
    name: str = "",
    branch: str = "",
    source_repo: str = "",
    create_branch: bool = False,
    start_point: str = "",
) -> None:
    _emit(
        GrokBuildCreateRejected(
            dispatch_id=dispatch_id,
            reason_code=reason_code,
            reason=reason,
            name=name,
            branch=branch,
            source_repo=source_repo,
            create_branch=create_branch,
            start_point=start_point,
        )
    )
