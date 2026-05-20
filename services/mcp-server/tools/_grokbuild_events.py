"""Event factories for grokbuild dispatch lifecycle."""

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


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)


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
