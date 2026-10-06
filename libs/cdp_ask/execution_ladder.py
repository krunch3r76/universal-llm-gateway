"""Execution finish ladder shared by first-run and resumed executions.

``finish_execution`` turns a ``run_execution`` payload into store transitions
(``awaiting_wake`` / ``completed`` / ``failed`` / ``aborted``), each of which
writes through to the registry row's ``execution_state``. ``guard_execution``
is the task boundary: any exception a run or resume escapes with becomes an
explicit terminal, so no execution is left ``running`` in memory with a row
still in flight.

One branch is new (a:36969 / a:36948 review note): a failure whose error reads
as the Chrome host dying under a live turn — while the row still has its
``chat_url`` — is not a terminal. The row is parked ``dormant`` and the record
goes to ``execution_resume`` with trigger ``host_lost``; the session at that
URL is where the answer is still being written. One resume per execution:
a second host loss terminalizes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from claude_bundles import cdp_registry

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.models import classify_stall_stage
from cdp_ask.runner import HarvestRootMismatchError
from cdp_ask.unverifiable import is_host_lost_error

logger = logging.getLogger(__name__)

MAX_HOST_LOSS_RESUMES = 1
# Harvest reclaim runs only for these purposes. Anything else leaves the row.
_HARVEST_DORMANT_PURPOSES = frozenset({"ask", "review"})

__all__ = ["MAX_HOST_LOSS_RESUMES", "finish_execution", "guard_execution"]


@dataclass(frozen=True)
class _HarvestSnap:
    """Pre-``mark_terminal`` view of followup occupancy.

    ``mark_terminal`` write-through replaces any in-flight ``execution_state``,
    including ``kind == "followup"``. Pre-check (ii) must use this snapshot.
    """

    followup_in_flight: bool


def _snapshot_harvest_row(registration_id: str) -> _HarvestSnap:
    row = cdp_registry._load_active().get(registration_id)
    if not isinstance(row, dict):
        return _HarvestSnap(False)
    from claude_bundles.cdp_registry.execution_state import followup_hold_in_flight

    entry = cdp_registry.row_execution_in_flight(row)
    followup = isinstance(entry, dict) and entry.get("kind") == "followup"
    if not followup:
        followup = followup_hold_in_flight(row)
    return _HarvestSnap(followup)


def _reclaim_completed_harvest(record: Any, snap: _HarvestSnap) -> None:
    """Park a still-active ask/review Chrome after a completed harvest.

    FORK A — terminal reached, cleanup bypassed in-process. This hook covers
    that fork: ``execution_state`` finished, row still active with chrome_pid.
    FORK B (boot reconcile / satellite restart) and FORK C (Stargate harvest
    only) are out of this function. Do not rank them here.

    Mirrors ``deregister_on_exit`` guard order as pre-checks only. Does not
    call ``_park_dormant`` (hard-coded ``idle_exit``) or ``deregister_on_exit``
    (``purpose_kill_default("ask")`` would release the row).

    Pre-check (ii) is the snapshot. Pre-checks (i) and (iii) re-read
    immediately before ``make_dormant``. A followup stamped between the
    snapshot and ``make_dormant`` is an accepted race: ``make_dormant`` has
    no followup refusal, and this hook does not re-check (ii).
    """
    registration_id = str(getattr(record, "registration_id", "") or "")
    if not registration_id:
        return
    if snap.followup_in_flight:
        logger.info(
            "harvest dormant skipped registration=%s reason=followup_in_flight",
            registration_id,
        )
        return
    row = cdp_registry._load_active().get(registration_id)
    if not isinstance(row, dict):
        logger.info(
            "harvest dormant skipped registration=%s reason=row_gone",
            registration_id,
        )
        return
    status = row.get("status")
    chrome_pid = row.get("chrome_pid")
    if status != "active" or chrome_pid is None:
        logger.info(
            "harvest dormant skipped registration=%s status=%s chrome_pid=%s",
            registration_id,
            status,
            chrome_pid,
        )
        return
    from claude_bundles.cse_wake_retain import registration_has_wake_debt

    if registration_has_wake_debt(registration_id):
        logger.info(
            "harvest dormant skipped registration=%s reason=wake_debt",
            registration_id,
        )
        return
    port = row.get("port")
    port_i = port if isinstance(port, int) else 0
    purpose = getattr(record, "purpose", None)
    from claude_bundles import cdp_registry_events as _events

    try:
        _events.emit(
            _events.cdp_port_exit_kill_decision(
                purpose=purpose if isinstance(purpose, str) else None,
                registration_id=registration_id,
                port=port_i,
                kill=False,
            )
        )
        seat = cdp_registry.make_dormant(registration_id, reason="bus_terminal_harvest")
    except cdp_registry.RegistryError:
        logger.info(
            "harvest dormant registry error registration=%s",
            registration_id,
        )
        return
    if seat is None:
        logger.info(
            "harvest dormant refused registration=%s reason=bus_terminal_harvest",
            registration_id,
        )
        return
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_port_harvest_dormant(
                registration_id=registration_id,
                purpose=purpose if isinstance(purpose, str) else None,
                port=seat.last_port if isinstance(seat.last_port, int) else port_i,
                reason="bus_terminal_harvest",
            )
        )


def _park_for_resume(registration_id: str) -> bool:
    """Park the dead-host row dormant so ``ensure_cse_attached`` relaunches it by URL.

    ``make_dormant`` refuses without a bound ``chat_url`` (nothing to resume)
    and leaves the row untouched; the caller then terminalizes as before.
    """
    row = cdp_registry._load_active().get(registration_id)
    if row is None:
        return False
    if row.get("status") == cdp_registry.STATUS_DORMANT:
        return True
    return cdp_registry.make_dormant(registration_id, reason="host_lost") is not None


async def _try_resume_after_host_loss(
    store: ExecutionStore, execution_id: str, error: str | None
) -> bool:
    """Route a host-loss failure to resume; False when the ordinary terminal applies."""
    record = await store.get(execution_id)
    if record is None or not record.registration_id:
        return False
    if record.abort_requested or record.resume_attempts >= MAX_HOST_LOSS_RESUMES:
        return False
    if not is_host_lost_error(error):
        return False
    parked = await asyncio.to_thread(_park_for_resume, record.registration_id)
    if not parked:
        return False
    record.resume_attempts += 1
    from cdp_ask.execution_resume import spawn_resume

    await spawn_resume(store, record, trigger="host_lost")
    return True


async def finish_execution(
    store: ExecutionStore, execution_id: str, payload: dict[str, Any]
) -> None:
    """Apply a run payload to the store — the single completion decision for an execution."""
    if payload.get("awaiting_wake_debt") and payload.get("ok"):
        await store.mark_awaiting_wake(execution_id, result=payload)
        return
    status = (
        "aborted"
        if payload.get("status") == "aborted"
        else ("completed" if payload.get("ok") else "failed")
    )
    error = payload.get("error")
    if status == "failed" and await _try_resume_after_host_loss(
        store, execution_id, error
    ):
        return
    stall = payload.get("stall_stage")
    if status == "failed" and not stall:
        stall = classify_stall_stage(error)
    harvest_record = None
    harvest_snap: _HarvestSnap | None = None
    if status == "completed":
        harvest_record = await store.get(execution_id)
        purpose = getattr(harvest_record, "purpose", None)
        registration_id = getattr(harvest_record, "registration_id", None)
        if (
            harvest_record is not None
            and purpose in _HARVEST_DORMANT_PURPOSES
            and registration_id
        ):
            harvest_snap = await asyncio.to_thread(
                _snapshot_harvest_row, str(registration_id)
            )
    await store.mark_terminal(
        execution_id,
        status=status,
        result=payload,
        error=error,
        stall_stage=stall if status == "failed" else None,
    )
    if harvest_record is not None and harvest_snap is not None:
        await asyncio.to_thread(
            _reclaim_completed_harvest, harvest_record, harvest_snap
        )


async def _abort_requested(store: ExecutionStore, execution_id: str) -> bool:
    record = await store.get(execution_id)
    return bool(record and record.abort_requested)


async def guard_execution(
    store: ExecutionStore,
    execution_id: str,
    body: Callable[[], Awaitable[None]],
) -> None:
    """Run *body* as the execution's task; every escape becomes an explicit terminal."""
    try:
        await body()
    except asyncio.CancelledError:
        if store.shutting_down and not await _abort_requested(store, execution_id):
            # Process teardown, not an operator abort: the row keeps its in-flight
            # execution_state and its Chrome so the successor hydrates and resumes.
            logger.info(
                "execution %s parked in flight for successor (process draining)",
                execution_id,
            )
            raise
        with contextlib.suppress(Exception):
            await store.mark_terminal(
                execution_id,
                status="aborted",
                error="cancelled",
                stall_stage="mark_terminal",
            )
        raise
    except HarvestRootMismatchError as exc:
        logger.exception("execution %s harvest root mismatch", execution_id)
        await store.mark_terminal(
            execution_id,
            status="failed",
            error=str(exc),
            stall_stage="mark_terminal",
        )
    except Exception as exc:  # noqa: BLE001 — task boundary
        logger.exception("execution %s failed", execution_id)
        # Through the ladder, not straight to terminal: a host that died under
        # the turn surfaces here as a raised Page.evaluate error, and that is
        # the resume case (a:36948 acceptance #4, 2026-09-30).
        record = await store.get(execution_id)
        await finish_execution(
            store,
            execution_id,
            {
                "ok": False,
                "status": "failed",
                "error": str(exc),
                "registration_id": record.registration_id if record else None,
                "stall_stage": "mark_terminal",
            },
        )
