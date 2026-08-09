"""Observable marks for silence-family victim classes (arc 6929 L5).

Disclosure signals only — they do not cure Auto-arm transport flakes.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

MARK_QUEUE_OWNER_RESTART_DEATH = "queue_owner_restart_death"
MARK_HOP_SUCCESSOR_NEVER_RAN = "hop_successor_never_ran"
MARK_LIVENESS_PROBE_SWALLOW = "liveness_probe_swallow_fail_open"


@event_factory
def GiwCursorAutoQueueOwnerRestartBusUnposted(  # noqa: N802
    job_id: str,
    thread_id: str,
    status_code: int | None,
) -> Event:
    """Ledger terminalized queue_owner_restart but bus notify did not land."""
    return Event(
        signal="giw.cursor_auto.queue_owner_restart_bus_unposted",
        payload={
            "job_id": job_id,
            "thread_id": thread_id,
            "status_code": status_code,
            "mark": MARK_QUEUE_OWNER_RESTART_DEATH,
            "bus_notify_mark": "queue_owner_restart_death",
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceSuccessionClaimMissingExecutionId(  # noqa: N802
    thread_id: str,
    job_id: str | None,
) -> Event:
    """Hop fire claimed succession without a joinable execution_id."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_succession_claim_missing_execution_id",
        payload={
            "thread_id": thread_id,
            "job_id": job_id,
            "mark": MARK_HOP_SUCCESSOR_NEVER_RAN,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceLivenessProbeFailed(  # noqa: N802
    thread_id: str,
    error: str,
) -> Event:
    """Liveness/capacity probe exception swallowed → empty snap fail-open."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_liveness_probe_failed",
        payload={
            "thread_id": thread_id,
            "error": error,
            "mark": MARK_LIVENESS_PROBE_SWALLOW,
            "fail_open": True,
        },
        scope="node",
        role="observation",
    )


def emit_queue_owner_restart_bus_unposted(
    *,
    job_id: str,
    thread_id: str,
    status_code: int | None,
) -> None:
    emit_frontier_event(
        GiwCursorAutoQueueOwnerRestartBusUnposted(
            job_id=job_id,
            thread_id=thread_id,
            status_code=status_code,
        )
    )
    logger.warning(
        "queue_owner_restart bus_unposted job=%s thread=%s status_code=%s",
        job_id,
        thread_id,
        status_code,
    )


def emit_succession_claim_missing_execution_id(
    *,
    thread_id: str,
    job_id: str | None = None,
) -> None:
    emit_frontier_event(
        GiwCursorAutoHopCadenceSuccessionClaimMissingExecutionId(
            thread_id=thread_id,
            job_id=job_id,
        )
    )
    logger.warning(
        "hop_cadence succession_claim_missing_execution_id thread=%s job=%s",
        thread_id,
        job_id,
    )


def emit_liveness_probe_failed(*, thread_id: str, error: str) -> None:
    emit_frontier_event(
        GiwCursorAutoHopCadenceLivenessProbeFailed(
            thread_id=thread_id,
            error=error,
        )
    )
    logger.warning(
        "hop_cadence liveness_probe_failed thread=%s error=%s",
        thread_id,
        error,
    )


__all__ = [
    "MARK_HOP_SUCCESSOR_NEVER_RAN",
    "MARK_LIVENESS_PROBE_SWALLOW",
    "MARK_QUEUE_OWNER_RESTART_DEATH",
    "emit_liveness_probe_failed",
    "emit_queue_owner_restart_bus_unposted",
    "emit_succession_claim_missing_execution_id",
]
