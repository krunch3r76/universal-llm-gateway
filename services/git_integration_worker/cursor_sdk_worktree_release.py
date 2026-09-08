"""Single chokepoint for Lane-B worktree removal (S1 Leg C)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    branch_state,
    is_worktree_dirty,
    salvage_commit,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
    worktree_held_by_live_bridge,
)
from services.git_integration_worker.cursor_sdk_worktree_lock import (
    ForeignLockError,
    parse_lock_reason,
    unlock_lane_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    DispatchWorktreeRecord,
    active_pin,
    lookup_dispatch_worktree,
    release_pin,
    unregister_lane_worktree,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_LIVE_DISPATCH_STATUSES = ("admitted", "running", "queued", "parked_waiting")
_unharvested_emitted: set[tuple[str, str]] = set()


class ReleaseRefusal(StrEnum):
    NOT_REGISTERED = "not_registered"
    LIVE_BRIDGE = "live_bridge"
    DISPATCH_ACTIVE = "dispatch_active"
    RETAIN_ACTIVE = "retain_active"
    UNHARVESTED = "unharvested"
    FOREIGN_LOCK = "foreign_lock"


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    released: bool
    refusal: ReleaseRefusal | None = None
    branch_retained: bool = False
    salvaged: bool = False
    head_sha: str | None = None
    salvage_refused: bool = False


def reset_unharvested_emit_dedupe() -> None:
    """Clear in-process UNHARVESTED emit dedupe (tests only)."""
    _unharvested_emitted.clear()


def emit_sdk_lane_b_worktree_removed(**kwargs: object) -> None:
    """Re-export sole worktree_removed emitter for routed callers."""
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_worktree_removed as _emit,
    )

    _emit(**kwargs)  # type: ignore[arg-type]


def _git_worktree_remove(source_repo: Path, worktree_path: Path) -> bool:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(source_repo.resolve()),
            "worktree",
            "remove",
            "--force",
            str(worktree_path.resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning(
            "worktree remove failed path=%s err=%s",
            worktree_path,
            proc.stderr.strip(),
        )
        return False
    return True


def _ledger_status_for_dispatch(*, dispatch_id: str) -> str:
    with ledger_connection() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None or row["status"] is None:
        return "none"
    return str(row["status"])


def _any_non_terminal_on_thread(*, thread_id: str) -> bool:
    placeholders = ", ".join("?" for _ in _LIVE_DISPATCH_STATUSES)
    with ledger_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM cursor_sdk_dispatches "
            f"WHERE thread_id=? AND status IN ({placeholders}) LIMIT 1",
            (thread_id, *_LIVE_DISPATCH_STATUSES),
        ).fetchone()
    return row is not None


def _landed_by_ancestry(
    *,
    source_repo: Path,
    branch_name: str,
    branch_point: str,
) -> bool:
    from services.git_integration_worker.cursor_sdk_deliverables_expected import (
        admit_landed_true,
    )

    state = branch_state(
        source_repo.resolve(),
        branch_name=branch_name,
        branch_point=branch_point,
    )
    ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(source_repo.resolve()),
            "merge-base",
            "--is-ancestor",
            branch_name,
            "master",
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    ancestry_on_master = ancestor.returncode == 0 if ancestor.returncode in (0, 1) else None
    landed = admit_landed_true(
        ancestry_on_master=ancestry_on_master,
        commits_ahead=state.commits_ahead,
    )
    return landed is True


def _emit_release_refused(
    *,
    reason: ReleaseRefusal,
    dispatch_id: str | None,
    thread_id: str | None,
    worktree_path: str,
) -> None:
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_release_refused,
    )

    if reason == ReleaseRefusal.UNHARVESTED:
        key = (worktree_path, reason.value)
        if key in _unharvested_emitted:
            return
        _unharvested_emitted.add(key)
    emit_sdk_lane_b_release_refused(
        reason=reason.value,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        worktree_path=worktree_path,
    )


def _resolve_record(
    *,
    source_repo: Path,
    dispatch_id: str | None,
    thread_id: str | None,
    worktree_path: Path | None,
) -> DispatchWorktreeRecord | None:
    if dispatch_id:
        record = lookup_dispatch_worktree(
            dispatch_id=dispatch_id,
            source_repo=source_repo,
        )
        if record is not None:
            return record
    if thread_id:
        from services.git_integration_worker.cursor_sdk_worktree_registry import (
            lookup_lane_worktree,
        )

        return lookup_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    if worktree_path is not None:
        with ledger_connection() as conn:
            row = conn.execute(
                "SELECT source_repo, thread_id, worktree_path, branch_name, "
                "branch_point, last_dispatch_id FROM cursor_sdk_lane_worktrees "
                "WHERE source_repo=? AND worktree_path=?",
                (str(source_repo.resolve()), str(worktree_path.resolve())),
            ).fetchone()
        if row is not None:
            return DispatchWorktreeRecord(
                worktree_path=Path(row["worktree_path"]),
                branch_name=row["branch_name"],
                branch_point=row["branch_point"],
                thread_id=str(row["thread_id"] or ""),
                last_dispatch_id=row["last_dispatch_id"],
                source_repo=str(row["source_repo"] or ""),
            )
    return None


def release_lane_worktree(
    *,
    source_repo: Path,
    reason: str,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
    worktree_path: Path | None = None,
    allow_unharvested: bool = False,
    unregistered: bool = False,
    actor: str | None = None,
) -> ReleaseResult:
    """Sole production caller of ``git worktree remove`` for Lane-B trees."""
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_pin_released,
        emit_sdk_lane_b_reap_skipped_live_bridge,
        emit_sdk_lane_b_salvage_failed,
    )
    from services.git_integration_worker.cursor_sdk_resume import dispatch_retain_active

    repo = source_repo.resolve()
    record = _resolve_record(
        source_repo=repo,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        worktree_path=worktree_path,
    )
    if record is None and not unregistered:
        if worktree_path is not None and worktree_path.is_dir():
            _emit_release_refused(
                reason=ReleaseRefusal.NOT_REGISTERED,
                dispatch_id=dispatch_id,
                thread_id=thread_id,
                worktree_path=str(worktree_path.resolve()),
            )
        return ReleaseResult(released=False, refusal=ReleaseRefusal.NOT_REGISTERED)

    wt_path = (
        worktree_path.resolve()
        if worktree_path is not None
        else record.worktree_path.resolve()
        if record is not None
        else None
    )
    if wt_path is None:
        return ReleaseResult(released=False, refusal=ReleaseRefusal.NOT_REGISTERED)

    resolved_dispatch = dispatch_id or (
        record.last_dispatch_id if record is not None else None
    )
    resolved_thread = thread_id or (record.thread_id if record is not None else None)

    holder_pid = worktree_held_by_live_bridge(worktree_path=wt_path, fresh=True)
    if holder_pid is not None:
        emit_sdk_lane_b_reap_skipped_live_bridge(
            worktree_path=str(wt_path),
            pid=holder_pid,
            dispatch_id=resolved_dispatch,
            stage="release",
        )
        _emit_release_refused(
            reason=ReleaseRefusal.LIVE_BRIDGE,
            dispatch_id=resolved_dispatch,
            thread_id=resolved_thread,
            worktree_path=str(wt_path),
        )
        return ReleaseResult(released=False, refusal=ReleaseRefusal.LIVE_BRIDGE)

    if not unregistered and resolved_dispatch and dispatch_retain_active(
        dispatch_id=resolved_dispatch
    ):
        _emit_release_refused(
            reason=ReleaseRefusal.RETAIN_ACTIVE,
            dispatch_id=resolved_dispatch,
            thread_id=resolved_thread,
            worktree_path=str(wt_path),
        )
        return ReleaseResult(released=False, refusal=ReleaseRefusal.RETAIN_ACTIVE)

    if not unregistered and resolved_thread and _any_non_terminal_on_thread(
        thread_id=resolved_thread
    ):
        _emit_release_refused(
            reason=ReleaseRefusal.DISPATCH_ACTIVE,
            dispatch_id=resolved_dispatch,
            thread_id=resolved_thread,
            worktree_path=str(wt_path),
        )
        return ReleaseResult(released=False, refusal=ReleaseRefusal.DISPATCH_ACTIVE)

    if not unregistered and not allow_unharvested and record is not None:
        pin = active_pin(source_repo=repo, thread_id=record.thread_id)
        ancestry = _landed_by_ancestry(
            source_repo=repo,
            branch_name=record.branch_name,
            branch_point=record.branch_point,
        )
        if pin is not None and not ancestry:
            _emit_release_refused(
                reason=ReleaseRefusal.UNHARVESTED,
                dispatch_id=resolved_dispatch,
                thread_id=resolved_thread,
                worktree_path=str(wt_path),
            )
            return ReleaseResult(released=False, refusal=ReleaseRefusal.UNHARVESTED)

    branch = record.branch_name if record is not None else None
    branch_point = record.branch_point if record is not None else ""
    ledger_status = (
        _ledger_status_for_dispatch(dispatch_id=resolved_dispatch)
        if resolved_dispatch
        else "none"
    )

    salvaged = False
    salvage = None
    if wt_path.is_dir() and is_worktree_dirty(wt_path) and record is not None:
        salvage = salvage_commit(
            wt_path,
            message=f"cursor-sdk: release salvage {resolved_dispatch or reason}",
        )
        salvaged = salvage.committed
        if salvage.refused:
            emit_sdk_lane_b_salvage_failed(
                dispatch_id=resolved_dispatch or "",
                branch=branch or "",
                worktree_path=str(wt_path),
                error=salvage.error,
            )
            return ReleaseResult(
                released=False,
                branch_retained=True,
                salvage_refused=True,
                head_sha=salvage.head_sha,
            )
        state_pre = branch_state(
            repo,
            branch_name=branch or "",
            branch_point=branch_point,
        )
        if (
            is_worktree_dirty(wt_path)
            and (state_pre.commits_ahead is None or state_pre.commits_ahead == 0)
            and not salvage.committed
        ):
            emit_sdk_lane_b_salvage_failed(
                dispatch_id=resolved_dispatch or "",
                branch=branch or "",
                worktree_path=str(wt_path),
                error="uncommitted work on empty branch",
            )
            return ReleaseResult(
                released=False,
                branch_retained=True,
                salvage_refused=True,
                head_sha=salvage.head_sha,
            )

    if wt_path.is_dir() and resolved_thread:
        try:
            if not unlock_lane_worktree(repo, wt_path, thread_id=resolved_thread):
                lock_reason = None
                from services.git_integration_worker.cursor_sdk_worktree_lock import (
                    list_locked_worktrees,
                )

                for entry in list_locked_worktrees(repo):
                    if entry.path == wt_path.resolve():
                        lock_reason = entry.reason
                        break
                if lock_reason and parse_lock_reason(lock_reason) is None:
                    _emit_release_refused(
                        reason=ReleaseRefusal.FOREIGN_LOCK,
                        dispatch_id=resolved_dispatch,
                        thread_id=resolved_thread,
                        worktree_path=str(wt_path),
                    )
                    return ReleaseResult(
                        released=False, refusal=ReleaseRefusal.FOREIGN_LOCK
                    )
                _emit_release_refused(
                    reason=ReleaseRefusal.FOREIGN_LOCK,
                    dispatch_id=resolved_dispatch,
                    thread_id=resolved_thread,
                    worktree_path=str(wt_path),
                )
                return ReleaseResult(
                    released=False, refusal=ReleaseRefusal.FOREIGN_LOCK
                )
        except ForeignLockError:
            _emit_release_refused(
                reason=ReleaseRefusal.FOREIGN_LOCK,
                dispatch_id=resolved_dispatch,
                thread_id=resolved_thread,
                worktree_path=str(wt_path),
            )
            return ReleaseResult(released=False, refusal=ReleaseRefusal.FOREIGN_LOCK)

    removed = False
    if wt_path.is_dir():
        removed = _git_worktree_remove(repo, wt_path)

    if removed:
        pin_reason = reason
        if reason.startswith("operator_release:"):
            pin_reason = "operator_release"
        if record is not None and resolved_thread:
            release_pin(
                source_repo=repo,
                thread_id=resolved_thread,
                dispatch_id=resolved_dispatch or record.last_dispatch_id or "",
                release_reason=pin_reason,
            )
            emit_sdk_lane_b_pin_released(
                dispatch_id=resolved_dispatch,
                thread_id=resolved_thread,
                worktree_path=str(wt_path),
                reason=pin_reason,
                actor=actor,
            )
            unregister_lane_worktree(
                thread_id=resolved_thread,
                source_repo=repo,
            )
        emit_sdk_lane_b_worktree_removed(
            worktree_path=str(wt_path),
            trigger=reason,
            ledger_status_at_remove=ledger_status,
            source_repo=str(repo),
            dispatch_id=resolved_dispatch,
            thread_id=resolved_thread,
            branch=branch,
        )

    branch_retained = False
    head_sha = None
    if record is not None and branch:
        state = branch_state(repo, branch_name=branch, branch_point=branch_point)
        head_sha = state.head_sha
        branch_retained = not state.safe_to_delete

    return ReleaseResult(
        released=removed,
        branch_retained=branch_retained,
        salvaged=salvaged,
        head_sha=head_sha,
    )


__all__ = [
    "ReleaseRefusal",
    "ReleaseResult",
    "emit_sdk_lane_b_worktree_removed",
    "release_lane_worktree",
    "reset_unharvested_emit_dedupe",
]
