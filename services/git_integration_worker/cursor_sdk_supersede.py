"""Interrupt a live nested cursor-sdk run so a newer dispatch can take the seat.

The SDK worker thread is non-cancellable from asyncio (see ``routes/cursor_sdk``
outer-timeout comment), so supersede works on the *bridge* instead: the live
``Run`` is registered here at ``agent.send`` time and interrupted with the
bridge ``CancelRun`` RPC, which unblocks ``run.wait()`` in the worker thread and
lets its ``finally`` release the capacity slot.

Escalation ladder — ``run.cancel()`` first, ``abort_orphaned_bridge`` (hard
subprocess close) only when cancel raised or the thread has not unwound within
the caller's grace window. Without the second rung a refused cancel would wedge
``cursor_sdk_gate`` at limit=1 and the superseding job could never run.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_cancel_events import (
    emit_sdk_worker_cancelled,
)
from services.git_integration_worker.cursor_sdk_orphan import abort_orphaned_bridge

logger = get_logger(__name__)


@dataclass(frozen=True)
class LiveRun:
    """A nested SDK run currently streaming inside a worker thread."""

    dispatch_id: str
    thread_id: str
    source_repo: str
    run: Any
    started_at: float


@dataclass(frozen=True)
class SupersedeMark:
    """Record that ``dispatch_id`` was interrupted in favour of a newer job."""

    dispatch_id: str
    superseded_by: str
    reason: str
    method: str
    marked_at: float

    def as_dict(self) -> dict[str, Any]:
        """Serializable view for bus payloads and closeout evidence."""
        return {
            "dispatch_id": self.dispatch_id,
            "superseded_by": self.superseded_by,
            "reason": self.reason,
            "method": self.method,
        }


_lock = threading.Lock()
_live: dict[str, LiveRun] = {}
_marks: dict[str, SupersedeMark] = {}


def register_live_run(
    *, dispatch_id: str, thread_id: str, source_repo: str, run: Any
) -> None:
    """Publish a streaming run so a same-thread supersede can reach it."""
    with _lock:
        _live[dispatch_id] = LiveRun(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            source_repo=source_repo,
            run=run,
            started_at=time.monotonic(),
        )


def unregister_live_run(*, dispatch_id: str) -> None:
    """Drop the run registration once the worker thread has left the stream."""
    with _lock:
        _live.pop(dispatch_id, None)


def live_run_for_thread(thread_id: str) -> LiveRun | None:
    """Return the live run owned by *thread_id*, newest first."""
    with _lock:
        candidates = [rec for rec in _live.values() if rec.thread_id == thread_id]
    if not candidates:
        return None
    return max(candidates, key=lambda rec: rec.started_at)


def is_dispatch_live(*, dispatch_id: str) -> bool:
    """True while the worker thread still owns the bridge stream."""
    with _lock:
        return dispatch_id in _live


def is_dispatch_superseded(*, dispatch_id: str) -> bool:
    """True once a newer same-thread dispatch has claimed this one's seat."""
    with _lock:
        return dispatch_id in _marks


def supersede_mark(*, dispatch_id: str) -> SupersedeMark | None:
    """Return the supersede record for *dispatch_id*, if any."""
    with _lock:
        return _marks.get(dispatch_id)


def clear_supersede(*, dispatch_id: str) -> None:
    """Forget the supersede record after the superseded episode is reported."""
    with _lock:
        _marks.pop(dispatch_id, None)


def signal_supersede(
    *, dispatch_id: str, superseded_by: str, reason: str
) -> dict[str, Any]:
    """Interrupt the live run for *dispatch_id* and record the supersede mark.

    Blocking: issues the bridge ``CancelRun`` unary from the calling thread.
    Returns evidence with ``method`` in ``run_cancel`` / ``bridge_abort`` /
    ``not_live`` so callers can log a falsifiable interrupt line.
    """
    with _lock:
        record = _live.get(dispatch_id)
    method = "not_live"
    error: str | None = None
    thread_id: str | None = record.thread_id if record is not None else None
    if record is not None:
        try:
            record.run.cancel()
            method = "run_cancel"
        except Exception as exc:  # noqa: BLE001 — any cancel refusal escalates
            error = f"{type(exc).__name__}: {exc}"
            method = (
                "bridge_abort"
                if abort_orphaned_bridge(dispatch_id=dispatch_id)
                else "cancel_failed"
            )
    mark = SupersedeMark(
        dispatch_id=dispatch_id,
        superseded_by=superseded_by,
        reason=reason,
        method=method,
        marked_at=time.monotonic(),
    )
    with _lock:
        _marks[dispatch_id] = mark
    logger.warning(
        "cursor-sdk supersede signalled dispatch_id=%s superseded_by=%s "
        "method=%s reason=%s error=%s",
        dispatch_id,
        superseded_by,
        method,
        reason,
        error,
    )
    emit_sdk_worker_cancelled(
        dispatch_id=dispatch_id,
        method=method,
        reason=reason,
        thread_id=thread_id,
        superseded_by=superseded_by,
        error=error,
    )
    return {**mark.as_dict(), "error": error, "thread_id": thread_id}


def escalate_supersede_abort(*, dispatch_id: str) -> bool:
    """Hard-close the bridge when a cancelled run has not released its slot."""
    with _lock:
        record = _live.get(dispatch_id)
        mark = _marks.get(dispatch_id)
    thread_id = record.thread_id if record is not None else None
    superseded_by = mark.superseded_by if mark is not None else None
    reason = mark.reason if mark is not None else "supersede_release_grace_exhausted"
    aborted = abort_orphaned_bridge(dispatch_id=dispatch_id)
    logger.warning(
        "cursor-sdk supersede escalated to bridge abort dispatch_id=%s aborted=%s",
        dispatch_id,
        aborted,
    )
    emit_sdk_worker_cancelled(
        dispatch_id=dispatch_id,
        method="bridge_abort_escalate" if aborted else "cancel_failed",
        reason=reason,
        thread_id=thread_id,
        superseded_by=superseded_by,
        error=None if aborted else "bridge_abort_returned_false",
    )
    return aborted
