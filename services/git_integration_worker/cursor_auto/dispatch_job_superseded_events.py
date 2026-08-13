"""``dispatch.job.superseded.notify`` — negative lifecycle for superseded seats."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

_SUPERSEDE_METHODS = frozenset(
    {
        "run_cancel",
        "bridge_abort",
        "pre_register_live_run",
        "queue_withdraw",
    }
)


@event_factory
def DispatchJobSupersededNotify(  # noqa: N802
    *,
    superseded_job_id: str,
    superseding_job_id: str,
    method: str,
    reason: str,
    thread_id: str,
    superseded_dispatch_id: str | None = None,
) -> Event:
    """Negative lifecycle signal when a same-thread job is displaced."""
    payload: dict[str, Any] = {
        "superseded_job_id": superseded_job_id,
        "superseding_job_id": superseding_job_id,
        "method": method,
        "reason": reason,
        "thread_id": thread_id,
    }
    if superseded_dispatch_id:
        payload["superseded_dispatch_id"] = superseded_dispatch_id
    return Event(
        signal="dispatch.job.superseded.notify",
        payload=payload,
        scope="node",
        role="observation",
    )


def emit_dispatch_job_superseded_notify(
    *,
    superseded_job_id: str,
    superseding_job_id: str,
    method: str,
    reason: str,
    thread_id: str,
    superseded_dispatch_id: str | None = None,
) -> None:
    """Publish supersede notify and log for poll / lane delivery correlation."""
    cleaned = str(method or "").strip()
    if cleaned not in _SUPERSEDE_METHODS:
        cleaned = "pre_register_live_run"
    event = DispatchJobSupersededNotify(
        superseded_job_id=superseded_job_id,
        superseding_job_id=superseding_job_id,
        method=cleaned,
        reason=reason,
        thread_id=thread_id,
        superseded_dispatch_id=superseded_dispatch_id,
    )
    emit_frontier_event(event)
    logger.warning(
        "dispatch job superseded notify superseded_job=%s superseding_job=%s "
        "thread=%s method=%s reason=%s dispatch_id=%s",
        superseded_job_id,
        superseding_job_id,
        thread_id,
        cleaned,
        reason,
        superseded_dispatch_id,
    )


__all__ = [
    "DispatchJobSupersededNotify",
    "emit_dispatch_job_superseded_notify",
]
