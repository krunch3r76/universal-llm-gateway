"""Event factories + emitters for grokbuild snapshot lifecycle (mcp.grokbuild.snapshot.*)."""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from grokbuild.events_core import _emit


@event_factory
def GrokBuildSnapshotCalled(  # noqa: N802
    dispatch_id: str,
    source_repo: str,
    slug: str,
    branch: str,
    reset_main: bool = False,
) -> Event:
    return Event(
        signal="mcp.grokbuild.snapshot.called",
        payload={
            "dispatch_id": dispatch_id,
            "source_repo": source_repo,
            "slug": slug,
            "branch": branch,
            "reset_main": reset_main,
        },
        scope="global",
    )


@event_factory
def GrokBuildSnapshotCompleted(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    source_repo: str,
    slug: str,
    branch: str,
    worktree_path: str,
    snapshot_sha: str,
    main_reset: str,
) -> Event:
    return Event(
        signal="mcp.grokbuild.snapshot.completed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "source_repo": source_repo,
            "slug": slug,
            "branch": branch,
            "worktree_path": worktree_path,
            "snapshot_sha": snapshot_sha,
            "main_reset": main_reset,
        },
        scope="global",
    )


@event_factory
def GrokBuildSnapshotFailed(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    error: str,
    source_repo: str,
    slug: str,
    branch: str,
) -> Event:
    return Event(
        signal="mcp.grokbuild.snapshot.failed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "error": error,
            "source_repo": source_repo,
            "slug": slug,
            "branch": branch,
        },
        scope="global",
    )


@event_factory
def GrokBuildSnapshotRejected(  # noqa: N802
    dispatch_id: str,
    reason_code: str,
    reason: str,
    source_repo: str = "",
    slug: str = "",
    branch: str = "",
) -> Event:
    return Event(
        signal="mcp.grokbuild.snapshot.rejected",
        payload={
            "dispatch_id": dispatch_id,
            "reason_code": reason_code,
            "reason": reason,
            "source_repo": source_repo,
            "slug": slug,
            "branch": branch,
        },
        scope="global",
    )


def emit_grok_build_snapshot_called(
    *,
    dispatch_id: str,
    source_repo: str,
    slug: str,
    branch: str,
    reset_main: bool = False,
) -> None:
    _emit(
        GrokBuildSnapshotCalled(
            dispatch_id=dispatch_id,
            source_repo=source_repo,
            slug=slug,
            branch=branch,
            reset_main=reset_main,
        )
    )


def emit_grok_build_snapshot_completed(
    *,
    dispatch_id: str,
    duration_s: float,
    source_repo: str,
    slug: str,
    branch: str,
    worktree_path: str,
    snapshot_sha: str,
    main_reset: str,
) -> None:
    _emit(
        GrokBuildSnapshotCompleted(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            source_repo=source_repo,
            slug=slug,
            branch=branch,
            worktree_path=worktree_path,
            snapshot_sha=snapshot_sha,
            main_reset=main_reset,
        )
    )


def emit_grok_build_snapshot_failed(
    *,
    dispatch_id: str,
    duration_s: float,
    error: str,
    source_repo: str,
    slug: str,
    branch: str,
) -> None:
    _emit(
        GrokBuildSnapshotFailed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            error=error,
            source_repo=source_repo,
            slug=slug,
            branch=branch,
        )
    )


def emit_grok_build_snapshot_rejected(
    *,
    dispatch_id: str,
    reason_code: str,
    reason: str,
    source_repo: str = "",
    slug: str = "",
    branch: str = "",
) -> None:
    _emit(
        GrokBuildSnapshotRejected(
            dispatch_id=dispatch_id,
            reason_code=reason_code,
            reason=reason,
            source_repo=source_repo,
            slug=slug,
            branch=branch,
        )
    )
