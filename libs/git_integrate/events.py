"""Event factories and emitters for git integration lifecycle signals."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event, event_factory

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None


def register_uds_publisher(publisher: Callable[[str, dict[str, Any]], None]) -> None:
    """Install a UDS publisher for lib events (worker-context use only)."""
    global _uds_publisher
    _uds_publisher = publisher


try:
    from mcp_events import record
except ImportError:

    def record(signal: str, **payload: Any) -> None:  # type: ignore[misc]
        if _uds_publisher is None:
            return
        _uds_publisher(signal, dict(payload))


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)


@event_factory
def GitIntegrateRequested(  # noqa: N802
    integration_id: str,
    arc: str,
    phase: str,
    worktree_path: str,
    diff_sha256: str,
) -> Event:
    return Event(
        signal="git.integrate.requested",
        payload={
            "integration_id": integration_id,
            "arc": arc,
            "phase": phase,
            "worktree_path": worktree_path,
            "diff_sha256": diff_sha256,
        },
        scope="global",
    )


@event_factory
def GitIntegrateCompleted(  # noqa: N802
    integration_id: str,
    arc: str,
    phase: str,
    merge_commit: str,
    master_sha: str,
    duration_s: float,
) -> Event:
    return Event(
        signal="git.integrate.completed",
        payload={
            "integration_id": integration_id,
            "arc": arc,
            "phase": phase,
            "merge_commit": merge_commit,
            "master_sha": master_sha,
            "duration_s": duration_s,
        },
        scope="global",
    )


@event_factory
def GitIntegrateRejected(  # noqa: N802
    integration_id: str,
    reason_code: str,
    reason: str,
    arc: str,
    phase: str,
) -> Event:
    return Event(
        signal="git.integrate.rejected",
        payload={
            "integration_id": integration_id,
            "reason_code": reason_code,
            "reason": reason,
            "arc": arc,
            "phase": phase,
        },
        scope="global",
    )


@event_factory
def GitIntegrateGateFailed(  # noqa: N802
    integration_id: str,
    arc: str,
    phase: str,
    gate_cmd: str,
    gate_exit: int,
    duration_s: float,
) -> Event:
    return Event(
        signal="git.integrate.gate.failed",
        payload={
            "integration_id": integration_id,
            "arc": arc,
            "phase": phase,
            "gate_cmd": gate_cmd,
            "gate_exit": gate_exit,
            "duration_s": duration_s,
        },
        scope="global",
    )


@event_factory
def GitIntegrateRetried(  # noqa: N802
    integration_id: str,
    arc: str,
    attempt: int,
    reason: str,
) -> Event:
    return Event(
        signal="git.integrate.retried",
        payload={
            "integration_id": integration_id,
            "arc": arc,
            "attempt": attempt,
            "reason": reason,
        },
        scope="global",
    )


@event_factory
def GitStatusRead(  # noqa: N802
    worktree_path: str,
    dirty: bool,
    branch: str,
) -> Event:
    return Event(
        signal="git.status.read",
        payload={
            "worktree_path": worktree_path,
            "dirty": dirty,
            "branch": branch,
        },
        scope="global",
    )


def emit_git_integrate_requested(
    *,
    integration_id: str,
    arc: str,
    phase: str,
    worktree_path: str,
    diff_sha256: str,
) -> None:
    _emit(
        GitIntegrateRequested(
            integration_id=integration_id,
            arc=arc,
            phase=phase,
            worktree_path=worktree_path,
            diff_sha256=diff_sha256,
        )
    )


def emit_git_integrate_completed(
    *,
    integration_id: str,
    arc: str,
    phase: str,
    merge_commit: str,
    master_sha: str,
    duration_s: float,
) -> None:
    _emit(
        GitIntegrateCompleted(
            integration_id=integration_id,
            arc=arc,
            phase=phase,
            merge_commit=merge_commit,
            master_sha=master_sha,
            duration_s=duration_s,
        )
    )


def emit_git_integrate_rejected(
    *,
    integration_id: str,
    reason_code: str,
    reason: str,
    arc: str,
    phase: str,
) -> None:
    _emit(
        GitIntegrateRejected(
            integration_id=integration_id,
            reason_code=reason_code,
            reason=reason,
            arc=arc,
            phase=phase,
        )
    )


def emit_git_integrate_gate_failed(
    *,
    integration_id: str,
    arc: str,
    phase: str,
    gate_cmd: str,
    gate_exit: int,
    duration_s: float,
) -> None:
    _emit(
        GitIntegrateGateFailed(
            integration_id=integration_id,
            arc=arc,
            phase=phase,
            gate_cmd=gate_cmd,
            gate_exit=gate_exit,
            duration_s=duration_s,
        )
    )


def emit_git_integrate_retried(
    *,
    integration_id: str,
    arc: str,
    attempt: int,
    reason: str,
) -> None:
    _emit(
        GitIntegrateRetried(
            integration_id=integration_id,
            arc=arc,
            attempt=attempt,
            reason=reason,
        )
    )


def emit_git_status_read(
    *,
    worktree_path: str,
    dirty: bool,
    branch: str,
) -> None:
    _emit(
        GitStatusRead(
            worktree_path=worktree_path,
            dirty=dirty,
            branch=branch,
        )
    )


@event_factory
def GitLandRequested(  # noqa: N802
    integration_id: str,
    arc: str,
    phase: str,
    worktree_path: str,
    diff_sha256: str,
    committed: bool,
) -> Event:
    return Event(
        signal="git.land.requested",
        payload={
            "integration_id": integration_id,
            "arc": arc,
            "phase": phase,
            "worktree_path": worktree_path,
            "diff_sha256": diff_sha256,
            "committed": committed,
        },
        scope="global",
    )


@event_factory
def GitLandCompleted(  # noqa: N802
    integration_id: str,
    arc: str,
    phase: str,
    merge_commit: str,
    master_sha: str,
    committed: bool,
    commit_sha: str,
    duration_s: float,
) -> Event:
    return Event(
        signal="git.land.completed",
        payload={
            "integration_id": integration_id,
            "arc": arc,
            "phase": phase,
            "merge_commit": merge_commit,
            "master_sha": master_sha,
            "committed": committed,
            "commit_sha": commit_sha,
            "duration_s": duration_s,
        },
        scope="global",
    )


@event_factory
def GitCommitCreated(  # noqa: N802
    integration_id: str,
    arc: str,
    commit_sha: str,
) -> Event:
    return Event(
        signal="git.commit.created",
        payload={
            "integration_id": integration_id,
            "arc": arc,
            "commit_sha": commit_sha,
        },
        scope="global",
    )


def emit_git_land_requested(
    *,
    integration_id: str,
    arc: str,
    phase: str,
    worktree_path: str,
    diff_sha256: str,
    committed: bool,
) -> None:
    _emit(
        GitLandRequested(
            integration_id=integration_id,
            arc=arc,
            phase=phase,
            worktree_path=worktree_path,
            diff_sha256=diff_sha256,
            committed=committed,
        )
    )


def emit_git_land_completed(
    *,
    integration_id: str,
    arc: str,
    phase: str,
    merge_commit: str,
    master_sha: str,
    committed: bool,
    commit_sha: str,
    duration_s: float,
) -> None:
    _emit(
        GitLandCompleted(
            integration_id=integration_id,
            arc=arc,
            phase=phase,
            merge_commit=merge_commit,
            master_sha=master_sha,
            committed=committed,
            commit_sha=commit_sha,
            duration_s=duration_s,
        )
    )


def emit_git_commit_created(
    *,
    integration_id: str,
    arc: str,
    commit_sha: str,
) -> None:
    _emit(
        GitCommitCreated(
            integration_id=integration_id,
            arc=arc,
            commit_sha=commit_sha,
        )
    )
