"""Worker-side admission + drain authority for git-integration-worker.

Single, loop-resident source of truth for in-flight mutating work and drain
state (Phase 1 of ``task:git-worker-event-driven-drain``). **No locks**: every
state mutation runs on the event loop, and correctness rests on there being
**no ``await`` between the drain-flag check and the in-flight reservation** —
single-loop cooperative scheduling makes that window atomic. This is the same
discipline ``universal_concurrency.FifoCapacityGate.try_acquire`` documents
("¬await between check and mutation = safe without lock"); here the reservation
is a controller-owned *ticket* rather than a gate slot, because the controller
is the count authority across BOTH the integrate gate and the cursor-sdk ledger.

Constructed once in the app lifespan and stashed on
``app.state.admission_controller``; routes reach it via the request, and the
cursor-sdk dispatch passes its ticket + controller explicitly into the on-loop
closeout. State is therefore never mutated from a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker import git_worker_drain_events as drain_events
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

logger = get_logger(__name__)

_TICKET_PENDING = "pending"
_TICKET_RUNNING = "running"


class Draining503(Exception):  # noqa: N818 — admission sentinel, caller maps to 503
    """Raised by ``try_admit`` when the worker is draining.

    The route handler catches this and returns the ``GIT_WORKER_DRAINING`` REST
    error envelope with a ``Retry-After`` header.
    """


@dataclass(slots=True)
class Ticket:
    """One reserved unit of mutating work, counted toward ``active_count``.

    Lifecycle: ``try_admit`` creates a ``pending`` ticket (already counted);
    the caller marks it ``running`` once it actually begins work, and closes it
    at terminal. ``admitted_at`` + ``drain_epoch_at_admit`` let the drain
    snapshot flag a race-admitted op for alerting (Phase 2).
    """

    kind: str
    op_id: str
    route: str
    admitted_at: datetime
    drain_epoch_at_admit: int
    state: str = _TICKET_PENDING
    _controller: WorkAdmissionController | None = field(default=None, repr=False)

    def mark_running(self) -> None:
        self.state = _TICKET_RUNNING

    def should_proceed(self) -> bool:
        """False once a drain has begun since this ticket was admitted.

        ``try_admit`` rejects new admissions while draining, so any live ticket
        was admitted *before* drain. The integrate route re-checks this once,
        right after the FIFO gate await and before starting work: if drain
        flipped during that wait the pending ticket aborts (releases the slot,
        closes the ticket, returns 503) instead of starting work — the
        "rejected" branch of the TOCTOU contract. A ticket that is already
        ``running`` is never re-checked and runs to completion.
        """
        ctrl = self._controller
        if ctrl is None:
            return True
        return not ctrl.is_draining()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "op_id": self.op_id,
            "route": self.route,
            "admitted_at": self.admitted_at.isoformat(),
            "state": self.state,
        }


@dataclass(slots=True)
class WorkAdmissionController:
    """Loop-resident admission + drain authority. Mutated on-loop only."""

    ledger: CursorDispatchLedger
    worker_id: str
    pid: int
    worker_started_at: str  # wall-clock ISO boot ts (cross-process generation id)
    _draining: bool = False
    _drain_epoch: int = 0
    _intent_id: str | None = None
    _drain_started_at: datetime | None = None
    _deadline_at: datetime | None = None
    _tickets: dict[str, Ticket] = field(default_factory=dict)
    _tracked_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    _completed_epochs: set[int] = field(default_factory=set)

    # ------------------------------------------------------------------ admit
    def try_admit(self, kind: str, *, op_id: str, route: str) -> Ticket:
        """SYNCHRONOUS admission. **No ``await`` between the drain check and the
        ticket reservation** — that is the whole atomicity guarantee.

        Raises ``Draining503`` while draining (after emitting
        ``git_worker.admission.rejected``). On success registers a ``pending``
        ticket that immediately counts toward ``active_count`` and returns it.
        Idempotent on ``op_id``: a re-admit for an op that already holds a live
        ticket returns the existing ticket (never double-counts).
        """
        if self._draining:
            drain_events.emit_admission_rejected(
                kind=kind,
                route=route,
                intent_id=self._intent_id,
                drain_epoch=self._drain_epoch,
            )
            raise Draining503(
                f"git-integration-worker is draining (epoch={self._drain_epoch})"
            )
        existing = self._tickets.get(op_id)
        if existing is not None:
            return existing
        ticket = Ticket(
            kind=kind,
            op_id=op_id,
            route=route,
            admitted_at=datetime.now(UTC),
            drain_epoch_at_admit=self._drain_epoch,
            _controller=self,
        )
        self._tickets[op_id] = ticket
        return ticket

    def is_draining(self) -> bool:
        return self._draining

    @property
    def drain_epoch(self) -> int:
        return self._drain_epoch

    # --------------------------------------------------------------- counting
    def active_ops(self) -> list[dict[str, Any]]:
        """Authoritative projection: counted tickets ∪ ledger live-running
        dispatches (orphan-excluded), de-duplicated by ``op_id`` so a cursor-sdk
        dispatch — which bridges a ticket and a ledger row — is counted once.

        Cursor-sdk rows carry ``resolved_model`` / ``subject_preview`` from the
        ledger so busy probes name the holder, not only an opaque dispatch id.
        """
        ops: list[dict[str, Any]] = []
        seen: set[str] = set()
        projections = {
            proj["op_id"]: proj for proj in self.ledger.live_dispatch_projections()
        }
        for ticket in self._tickets.values():
            entry = ticket.to_dict()
            proj = projections.get(ticket.op_id)
            if proj is not None:
                for key in (
                    "resolved_model",
                    "subject_preview",
                    "thread_id",
                    "started_at",
                ):
                    if proj.get(key) is not None and entry.get(key) is None:
                        entry[key] = proj[key]
            ops.append(entry)
            seen.add(ticket.op_id)
        for dispatch_id, proj in projections.items():
            if dispatch_id in seen:
                continue
            ops.append(proj)
            seen.add(dispatch_id)
        return ops

    def active_count(self) -> int:
        return len(self.active_ops())

    # ------------------------------------------------------------------ drain
    def next_epoch(self) -> int:
        return self._drain_epoch + 1

    def begin_drain(
        self,
        *,
        reason: str,
        intent_id: str,
        drain_epoch: int,
        deadline_s: float | None = None,
    ) -> dict[str, Any]:
        """Enter the drain epoch (idempotent on ``intent_id``+``drain_epoch``).

        Sets the drain flag/epoch and emits ``git_worker.drain.started`` exactly
        once per epoch. **Never clears the flag** — in Phase 1 it clears only by
        process restart (no un-drain admin op yet). If the worker is already
        idle at drain start, ``git_worker.drain.completed`` is emitted promptly
        via the idle re-check (epoch-guarded), so a manage supervisor that
        begins a drain on a quiescent worker is not left waiting on an event
        that a 1→0 transition would otherwise never produce.
        """
        if (
            self._draining
            and self._intent_id == intent_id
            and self._drain_epoch == drain_epoch
        ):
            return self.drain_state()  # idempotent re-drive, no re-emit
        self._draining = True
        self._drain_epoch = drain_epoch
        self._intent_id = intent_id
        self._drain_started_at = datetime.now(UTC)
        self._deadline_at = (
            self._drain_started_at + _timedelta_s(deadline_s)
            if deadline_s is not None
            else None
        )
        drain_events.emit_drain_started(
            reason=reason,
            intent_id=intent_id,
            drain_epoch=drain_epoch,
            worker_id=self.worker_id,
            pid=self.pid,
            worker_started_at=self.worker_started_at,
            active_count=self.active_count(),
            active_ops=self.active_ops(),
        )
        self._maybe_emit_drain_completed()
        return self.drain_state()

    def close_ticket(self, op_id: str, *, terminal_status: str) -> None:
        """Remove a ticket and, if that drops active work to zero while
        draining, emit ``git_worker.drain.completed`` exactly once for the
        epoch. For cursor-sdk, call this AFTER ``ledger.mark_terminal`` so the
        recomputed ``active_count`` no longer sees the dispatch's ledger row.
        Always invoked on-loop (the async closeout), never from a worker thread.
        """
        ticket = self._tickets.pop(op_id, None)
        if ticket is None:
            return
        logger.debug(
            "admission ticket closed: op_id=%s kind=%s terminal=%s",
            op_id,
            ticket.kind,
            terminal_status,
        )
        self._maybe_emit_drain_completed()

    def create_tracked_task(
        self, coro: Coroutine[Any, Any, Any], *, op_id: str
    ) -> asyncio.Task[Any]:
        """The ONLY sanctioned way to spawn fire-and-forget background work from
        a mutating route (enforced by the AC-8 static scan). Tracks the task so
        its completion both discards the handle and re-checks idle/drain.
        """
        task = asyncio.create_task(coro, name=f"tracked-{op_id}")
        self._tracked_tasks.add(task)

        def _on_done(done: asyncio.Task[Any]) -> None:
            self._tracked_tasks.discard(done)
            self._maybe_emit_drain_completed()

        task.add_done_callback(_on_done)
        return task

    def drain_state(self) -> dict[str, Any]:
        """One-shot snapshot for Phase-2's final epoch-check before SIGTERM."""
        return {
            "draining": self._draining,
            "drain_epoch": self._drain_epoch,
            "intent_id": self._intent_id,
            "worker_id": self.worker_id,
            "pid": self.pid,
            "worker_started_at": self.worker_started_at,
            "active_count": self.active_count(),
            "active_ops": self.active_ops(),
            "deadline_at": self._deadline_at.isoformat() if self._deadline_at else None,
        }

    async def wait_idle(self, timeout_s: float) -> bool:
        """Await until ``active_count()==0`` or timeout. Lifespan shutdown hook
        only (defense-in-depth); its effectiveness depends on Phase-2's extended
        git-worker SIGTERM budget.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while self.active_count() > 0:
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.1)
        return True

    # --------------------------------------------------------------- internal
    def _maybe_emit_drain_completed(self) -> None:
        if not self._draining:
            return
        if self._drain_epoch in self._completed_epochs:
            return
        if self.active_count() != 0:
            return
        self._completed_epochs.add(self._drain_epoch)
        drain_events.emit_drain_completed(
            intent_id=self._intent_id,
            drain_epoch=self._drain_epoch,
            worker_id=self.worker_id,
            pid=self.pid,
            completed_at=datetime.now(UTC).isoformat(),
            active_count=0,
        )


def _timedelta_s(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)
