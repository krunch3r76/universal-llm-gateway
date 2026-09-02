"""Observable marks for the restart-reconcile rehydrate path (mission 9440)."""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)


@event_factory
def GiwCursorAutoReconcileRehydrated(  # noqa: N802
    job_id: str,
    thread_id: str,
    generation: int,
    restart_intent_id: str | None,
) -> Event:
    """A queued-never-claimed row survived a GIW restart and is live again."""
    return Event(
        signal="giw.cursor_auto.reconcile.rehydrated",
        payload={
            "job_id": job_id,
            "thread_id": thread_id,
            "generation": generation,
            "restart_intent_id": restart_intent_id,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoReconcileSuperseded(  # noqa: N802
    job_id: str,
    thread_id: str,
    successor_job_id: str,
    generation: int,
) -> Event:
    """A rehydrate-eligible row was terminalized because a same-thread
    successor already exists (S-2 ii) — it never re-entered the live queue.
    """
    return Event(
        signal="giw.cursor_auto.reconcile.superseded_by_successor",
        payload={
            "job_id": job_id,
            "thread_id": thread_id,
            "successor_job_id": successor_job_id,
            "generation": generation,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoReconcileRehydrateExhausted(  # noqa: N802
    job_id: str,
    thread_id: str,
    generation: int,
) -> Event:
    """A row hit the rehydrate generation cap and was terminalized for real."""
    return Event(
        signal="giw.cursor_auto.reconcile.rehydrate_exhausted",
        payload={
            "job_id": job_id,
            "thread_id": thread_id,
            "generation": generation,
        },
        scope="node",
        role="observation",
    )


def emit_reconcile_rehydrated(
    *, job_id: str, thread_id: str, generation: int, restart_intent_id: str | None
) -> None:
    emit_frontier_event(
        GiwCursorAutoReconcileRehydrated(
            job_id=job_id,
            thread_id=thread_id,
            generation=generation,
            restart_intent_id=restart_intent_id,
        )
    )
    logger.info(
        "cursor-auto reconcile rehydrated job=%s thread=%s generation=%s",
        job_id,
        thread_id,
        generation,
    )


def emit_reconcile_superseded(
    *, job_id: str, thread_id: str, successor_job_id: str, generation: int
) -> None:
    emit_frontier_event(
        GiwCursorAutoReconcileSuperseded(
            job_id=job_id,
            thread_id=thread_id,
            successor_job_id=successor_job_id,
            generation=generation,
        )
    )
    logger.info(
        "cursor-auto reconcile superseded_by_successor job=%s thread=%s successor=%s",
        job_id,
        thread_id,
        successor_job_id,
    )


def emit_reconcile_rehydrate_exhausted(
    *, job_id: str, thread_id: str, generation: int
) -> None:
    emit_frontier_event(
        GiwCursorAutoReconcileRehydrateExhausted(
            job_id=job_id, thread_id=thread_id, generation=generation
        )
    )
    logger.warning(
        "cursor-auto reconcile rehydrate_exhausted job=%s thread=%s generation=%s",
        job_id,
        thread_id,
        generation,
    )


@event_factory
def GiwCursorAutoReconcileInflightLost(  # noqa: N802
    job_id: str,
    thread_id: str,
    dispatch_id: str,
) -> Event:
    """A claimed+dispatched row had no bus closeout at startup honor consult."""
    return Event(
        signal="giw.cursor_auto.reconcile.inflight_lost",
        payload={
            "job_id": job_id,
            "thread_id": thread_id,
            "dispatch_id": dispatch_id,
        },
        scope="node",
        role="observation",
    )


def emit_reconcile_inflight_lost(
    *, job_id: str, thread_id: str, dispatch_id: str
) -> None:
    emit_frontier_event(
        GiwCursorAutoReconcileInflightLost(
            job_id=job_id,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
        )
    )
    logger.warning(
        "cursor-auto reconcile inflight_lost job=%s thread=%s dispatch=%s",
        job_id,
        thread_id,
        dispatch_id,
    )


@event_factory
def GiwCursorAutoReconcileInflightLostBusUnposted(  # noqa: N802
    job_id: str,
    thread_id: str,
    dispatch_id: str,
    status_code: int | None,
) -> Event:
    """The inflight_lost terminal died with no waiter-visible bus turn.

    Distinct signal from ``queue_owner_restart.bus_unposted`` — this row was
    dispatched (unlike a never-dispatched claim), so the same reason string
    would mislabel telemetry on a mixed reconcile batch.
    """
    return Event(
        signal="giw.cursor_auto.reconcile.inflight_lost_bus_unposted",
        payload={
            "job_id": job_id,
            "thread_id": thread_id,
            "dispatch_id": dispatch_id,
            "status_code": status_code,
        },
        scope="node",
        role="observation",
    )


def emit_reconcile_inflight_lost_bus_unposted(
    *, job_id: str, thread_id: str, dispatch_id: str, status_code: int | None
) -> None:
    emit_frontier_event(
        GiwCursorAutoReconcileInflightLostBusUnposted(
            job_id=job_id,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            status_code=status_code,
        )
    )
    logger.warning(
        "cursor-auto reconcile inflight_lost bus_unposted job=%s thread=%s "
        "dispatch=%s status_code=%s",
        job_id,
        thread_id,
        dispatch_id,
        status_code,
    )


__all__ = [
    "emit_reconcile_inflight_lost",
    "emit_reconcile_inflight_lost_bus_unposted",
    "emit_reconcile_rehydrate_exhausted",
    "emit_reconcile_rehydrated",
    "emit_reconcile_superseded",
]
