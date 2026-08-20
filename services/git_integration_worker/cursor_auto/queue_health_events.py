"""Observation signals for Auto queue-health gauge transitions.

Rising-edge only — ``queue_admission_health`` is on the liveness poll path
and must not emit every probe.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)


@event_factory
def GiwCursorAutoQueueNotServing(  # noqa: N802
    red_reason: str,
    admit_eligible_pending: int,
    oldest_waiter_age_s: float | None,
    occupant_idle_s: float | None,
    serial_occupant_job_id: str | None,
) -> Event:
    """Queue is not serving: occupant stall, waiter starvation, or both."""
    return Event(
        signal="giw.cursor_auto.queue_not_serving",
        payload={
            "red_reason": red_reason,
            "admit_eligible_pending": admit_eligible_pending,
            "oldest_waiter_age_s": oldest_waiter_age_s,
            "occupant_idle_s": occupant_idle_s,
            "serial_occupant_job_id": serial_occupant_job_id,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoConcurrentClaimed(  # noqa: N802
    job_id: str,
    thread_id: str,
    contract: str,
    execution_mode: str,
) -> Event:
    """Concurrent worker claimed a lease-exempt Auto job beside the serial slot."""
    return Event(
        signal="giw.cursor_auto.concurrent_claimed",
        payload={
            "job_id": job_id,
            "thread_id": thread_id,
            "contract": contract,
            "execution_mode": execution_mode,
        },
        scope="node",
        role="observation",
    )


def emit_queue_not_serving(
    *,
    red_reason: str,
    admit_eligible_pending: int,
    oldest_waiter_age_s: float | None,
    occupant_idle_s: float | None,
    serial_occupant_job_id: str | None,
) -> None:
    """Publish queue-not-serving once per rising edge; logs the same payload."""
    emit_frontier_event(
        GiwCursorAutoQueueNotServing(
            red_reason=red_reason,
            admit_eligible_pending=admit_eligible_pending,
            oldest_waiter_age_s=oldest_waiter_age_s,
            occupant_idle_s=occupant_idle_s,
            serial_occupant_job_id=serial_occupant_job_id,
        )
    )
    logger.warning(
        "cursor-auto queue not serving reason=%s pending=%s "
        "oldest_waiter_age_s=%s occupant_idle_s=%s occupant=%s",
        red_reason,
        admit_eligible_pending,
        oldest_waiter_age_s,
        occupant_idle_s,
        serial_occupant_job_id,
    )


def emit_concurrent_claimed(
    *,
    job_id: str,
    thread_id: str,
    contract: str,
    execution_mode: str,
) -> None:
    """Publish that the concurrent worker claimed a lease-exempt Auto job."""
    emit_frontier_event(
        GiwCursorAutoConcurrentClaimed(
            job_id=job_id,
            thread_id=thread_id,
            contract=contract,
            execution_mode=execution_mode,
        )
    )
    logger.info(
        "cursor-auto concurrent claimed job=%s thread=%s contract=%s mode=%s",
        job_id,
        thread_id,
        contract,
        execution_mode,
    )


__all__ = ["emit_concurrent_claimed", "emit_queue_not_serving"]
