"""Park a live cursor-sdk dispatch so a GIW restart can drain without killing it.

Steer-restart v1 (spec ``cursor-sdk-steer-restart-v1`` D1–D4). A running
dispatch holds three ephemeral things — a bridge process, a worker thread, an
admission ticket — while its *work* already lives in durable stores (SDK agent
store, lane worktree, ledger row). ``signal_park`` detaches the ephemeral
holders with the store left consistent: it issues the bridge ``CancelRun`` via
``run.cancel()`` (the supersede ladder without the Lane-B disposition stamp),
which unblocks ``run.wait()`` in the worker thread; the gated coroutine then
sees the in-process park mark and finalizes the row as terminal ``cancelled``
plus park columns (``cursor_sdk_closeout.park_finalize``). GIW startup
re-admits it as a ``resume_of`` child (``cursor_sdk_park_resume``).

The park mark is process-local like the supersede mark; a crash between mark
and finalize degrades to the existing ``running_orphans`` path (row failed with
partial harvest) — never silent. Refusal ladder: ``cursor_sdk_park_preflight``.
Sweep for a restart intent: ``cursor_sdk_park_sweep``. Bridge-close
convergence: ``cursor_sdk_park_converge``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_cancel_events import (
    emit_sdk_worker_cancelled,
)
from services.git_integration_worker.cursor_sdk_orphan import abort_orphaned_bridge
from services.git_integration_worker.cursor_sdk_park_events import (
    emit_sdk_park_refused,
    emit_sdk_park_requested,
)
from services.git_integration_worker.cursor_sdk_park_preflight import (
    TERMINAL_RUN_STATUSES,
    ParkRefusal,
    preflight_park,
)
from services.git_integration_worker.cursor_sdk_supersede import live_run_for_dispatch

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ParkMark:
    """In-process record that ``dispatch_id``'s run was cancelled for a park."""

    dispatch_id: str
    thread_id: str | None
    intent_id: str | None
    drain_epoch: int | None
    actor: str
    reason: str
    requested_at: str
    method: str
    marked_at: float

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k != "marked_at"}


_lock = threading.Lock()
_marks: dict[str, ParkMark] = {}


def park_mark(dispatch_id: str) -> ParkMark | None:
    """Return the park mark when this process cancelled the run for a park."""
    with _lock:
        return _marks.get(dispatch_id)


def clear_park_mark(dispatch_id: str) -> None:
    with _lock:
        _marks.pop(dispatch_id, None)


def reset_park_marks() -> None:
    """Tests only."""
    with _lock:
        _marks.clear()


@dataclass(frozen=True, slots=True)
class ParkSignalResult:
    dispatch_id: str
    thread_id: str | None
    refusal: ParkRefusal | None
    already_parked: bool = False
    method: str | None = None
    error: str | None = None
    sdk_agent_id_present: bool = False

    @property
    def requested(self) -> bool:
        return self.refusal is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dispatch_id": self.dispatch_id,
            "thread_id": self.thread_id,
            "park_state": (
                "park_requested"
                if self.requested
                else ("already_parked" if self.already_parked else "refused")
            ),
            "refusal": self.refusal.value if self.refusal else None,
            "method": self.method,
            "error": self.error,
            "sdk_agent_id_present": self.sdk_agent_id_present,
        }


def _refuse(
    *,
    dispatch_id: str,
    thread_id: str | None,
    refusal: ParkRefusal,
    intent_id: str | None,
    actor: str,
    already_parked: bool = False,
    error: str | None = None,
    sdk_agent_id_present: bool = False,
) -> ParkSignalResult:
    if not already_parked:
        emit_sdk_park_refused(
            dispatch_id=dispatch_id,
            refusal=refusal.value,
            intent_id=intent_id,
            actor=actor,
        )
    return ParkSignalResult(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        refusal=refusal,
        already_parked=already_parked,
        error=error,
        sdk_agent_id_present=sdk_agent_id_present,
    )


def signal_park(
    dispatch_id: str,
    *,
    intent_id: str | None,
    drain_epoch: int | None,
    actor: str,
    reason: str,
) -> ParkSignalResult:
    """Preflight, mark, and cancel the live run for *dispatch_id*.

    Blocking (bridge ``CancelRun`` unary). Cancel ladder: ``run.cancel()`` →
    on a raised refusal ``abort_orphaned_bridge`` (method ``bridge_abort``) →
    ``CANCEL_FAILED`` when both fail (mark cleared, row untouched). Never
    stamps a Lane-B disposition: the tree stays pinned for the resume child.
    """
    pre = preflight_park(dispatch_id, intent_id=intent_id)
    thread_id = (
        str(pre.row.get("thread_id")) if pre.row and pre.row.get("thread_id") else None
    )
    agent_present = bool(pre.row and pre.row.get("sdk_agent_id"))
    if pre.refusal is not None:
        return _refuse(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            refusal=pre.refusal,
            intent_id=intent_id,
            actor=actor,
            already_parked=pre.already_parked,
            error=pre.detail,
            sdk_agent_id_present=agent_present,
        )
    existing = park_mark(dispatch_id)
    if existing is not None:
        return ParkSignalResult(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            refusal=None,
            method=existing.method,
            sdk_agent_id_present=True,
        )
    record = live_run_for_dispatch(dispatch_id)
    if record is None:
        return _refuse(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            refusal=ParkRefusal.NOT_LIVE_HERE,
            intent_id=intent_id,
            actor=actor,
            sdk_agent_id_present=True,
        )
    run_status = getattr(record.run, "status", None)
    if isinstance(run_status, str) and run_status in TERMINAL_RUN_STATUSES:
        return _refuse(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            refusal=ParkRefusal.RUN_ALREADY_TERMINAL,
            intent_id=intent_id,
            actor=actor,
            error=f"run status={run_status!r}",
            sdk_agent_id_present=True,
        )
    requested_at = _now()
    emit_sdk_park_requested(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        intent_id=intent_id,
        actor=actor,
    )
    error: str | None = None
    try:
        record.run.cancel()
        method = "run_cancel"
    except Exception as exc:  # noqa: BLE001 — any cancel refusal escalates one rung
        error = f"{type(exc).__name__}: {exc}"
        if abort_orphaned_bridge(dispatch_id=dispatch_id):
            method = "bridge_abort"
        else:
            logger.warning(
                "cursor-sdk park cancel ladder exhausted dispatch_id=%s error=%s",
                dispatch_id,
                error,
            )
            return _refuse(
                dispatch_id=dispatch_id,
                thread_id=thread_id,
                refusal=ParkRefusal.CANCEL_FAILED,
                intent_id=intent_id,
                actor=actor,
                error=error,
                sdk_agent_id_present=True,
            )
    mark = ParkMark(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        intent_id=intent_id,
        drain_epoch=drain_epoch,
        actor=actor,
        reason=reason,
        requested_at=requested_at,
        method=method,
        marked_at=time.monotonic(),
    )
    with _lock:
        _marks[dispatch_id] = mark
    emit_sdk_worker_cancelled(
        dispatch_id=dispatch_id,
        method=method,
        reason=f"park_for_restart:{intent_id}" if intent_id else "park_for_restart",
        thread_id=thread_id,
        error=error,
    )
    logger.warning(
        "cursor-sdk park signalled dispatch_id=%s intent_id=%s method=%s actor=%s",
        dispatch_id,
        intent_id,
        method,
        actor,
    )
    return ParkSignalResult(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        refusal=None,
        method=method,
        error=error,
        sdk_agent_id_present=True,
    )


__all__ = [
    "ParkMark",
    "ParkRefusal",
    "ParkSignalResult",
    "clear_park_mark",
    "park_mark",
    "reset_park_marks",
    "signal_park",
]
