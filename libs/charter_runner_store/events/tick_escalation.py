"""Charter tick health escalation signal (episode open / TTL re-fire)."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def ManageCharterTickEscalation(  # noqa: N802
    root: str,
    fire_attempt_outcome: str | None,
    fire_attempt_reason: str | None,
    worker_thread: str | None = None,
    refired: bool = False,
) -> Event:
    """Episode opened or TTL re-fired for an unhealthy enrolled root."""
    payload: dict[str, object] = {
        "root": root,
        "fire_attempt_outcome": fire_attempt_outcome,
        "fire_attempt_reason": fire_attempt_reason,
        "refired": refired,
    }
    if worker_thread:
        payload["worker_thread"] = worker_thread
    return Event(
        signal="manage.charter.tick.escalation",
        payload=payload,
        scope="global",
    )


__all__ = ["ManageCharterTickEscalation"]
