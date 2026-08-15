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
import os
import time
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

# Relay tickets are minted by the cursor-auto caller *before* it POSTs the nested
# dispatch, so this route is the one admission path whose ticket can outlive the
# work it reserved: a submission that never reaches the worker leaves no ledger
# row to retire it. See ``_leaked_relay_op_ids``.
_RELAY_ROUTE = "cursor-auto/nested"
_RELAY_LEAK_GRACE_S_DEFAULT = 900.0


def _relay_leak_grace_s() -> float:
    raw = os.environ.get("CURSOR_RELAY_TICKET_LEAK_GRACE_S", "").strip()
    if not raw:
        return _RELAY_LEAK_GRACE_S_DEFAULT
    try:
        return max(60.0, float(raw))
    except ValueError:
        return _RELAY_LEAK_GRACE_S_DEFAULT


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
    _drain_started_monotonic: float | None = None
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
    def _leaked_relay_op_ids(self) -> list[str]:
        """Relay tickets whose nested dispatch never reached the ledger.

        ``submit_nested_dispatch`` reserves its ticket *before* POSTing, so this
        is the one admission path whose reservation can outlive the work: a
        submission rejected pre-admission (``CURSOR_WORKTREE_MINT_FAILED``) or
        lost in transport leaves no row to retire it, and the ticket then counts
        toward ``active_count`` forever — wedging drain convergence, which no
        operator surface can clear because ``force`` is barred on this worker.

        Absence of a ledger row — not age alone — is the leak signature. A queued
        or running nested dispatch always has one under the same ``dispatch_id``,
        so this never reaps live work. The grace period only covers the window
        between the caller's reservation and the worker's ledger insert.
        """
        now = datetime.now(UTC)
        grace_s = _relay_leak_grace_s()
        leaked: list[str] = []
        for ticket in self._tickets.values():
            if ticket.route != _RELAY_ROUTE or ticket.state != _TICKET_PENDING:
                continue
            if (now - ticket.admitted_at).total_seconds() < grace_s:
                continue
            if self.ledger.dispatch_status_by_id(dispatch_id=ticket.op_id) is None:
                leaked.append(ticket.op_id)
        return leaked

    def _reap_leaked_relay_tickets(self) -> None:
        for op_id in self._leaked_relay_op_ids():
            ticket = self._tickets.pop(op_id, None)
            if ticket is None:
                continue
            logger.warning(
                "reaped leaked relay admission ticket: op_id=%s route=%s admitted_at=%s",
                op_id,
                ticket.route,
                ticket.admitted_at.isoformat(),
            )

    def active_ops(self) -> list[dict[str, Any]]:
        """Authoritative projection: counted tickets ∪ ledger live-running
        dispatches (orphan-excluded), de-duplicated by ``op_id`` so a cursor-sdk
        dispatch — which bridges a ticket and a ledger row — is counted once.

        Cursor-sdk rows carry ``resolved_model`` / ``subject_preview`` from the
        ledger so busy probes name the holder, not only an opaque dispatch id.

        Leaked relay tickets are reaped here rather than in a background sweep so
        that every count authority — busy probes, ``drain_state``, and the idle
        re-check — reconciles on read; the ledger side already applies the same
        live-task filter in ``live_dispatch_projections``.
        """
        self._reap_leaked_relay_tickets()
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
        once per epoch. The flag clears on process restart OR via
        ``release_drain`` / ``POST .../cancel-drain`` when manage cancels the
        matching restart intent (survival condition 1). If the worker is already
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
        self._drain_started_monotonic = time.monotonic()
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

    def release_drain(self, *, intent_id: str, drain_epoch: int) -> dict[str, Any]:
        """Clear ``_draining`` when ``(intent_id, drain_epoch)`` matches current.

        Idempotent no-op on mismatch or when not draining — returns the current
        ``drain_state()`` either way. Does not SIGTERM or mutate tickets; the
        manage cancel path calls this so admission reopens without process death
        (pairing requirement with store ``cancel``).
        """
        if (
            self._draining
            and self._intent_id == intent_id
            and self._drain_epoch == drain_epoch
        ):
            self._draining = False
            self._intent_id = None
            self._deadline_at = None
            self._drain_started_at = None
            self._drain_started_monotonic = None
            logger.info(
                "drain released without SIGTERM: intent_id=%s drain_epoch=%s",
                intent_id,
                drain_epoch,
            )
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
            "drain_started_at": (
                self._drain_started_at.isoformat() if self._drain_started_at else None
            ),
            "drain_started_monotonic": self._drain_started_monotonic,
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
