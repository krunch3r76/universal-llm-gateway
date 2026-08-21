"""@event_factory vocabulary for the GIW recycle sliver (manage.recycle.*).

Catalog-visible (libs/ is walked by gen-event-catalog). Emitters in
``scripts/model_manager/observation_event.py`` instantiate these factories then
publish on the manage UDS path so drain and recycle share one observation seam.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

_SERVICE = "git_integration_worker"


@event_factory
def ManageRecycleRequested(  # noqa: N802
    *,
    service: str = _SERVICE,
    intent_id: str | None = None,
) -> Event:
    """Life or code seat asked manage to recycle git_integration_worker."""
    payload: dict[str, Any] = {"service": service}
    if intent_id is not None:
        payload["intent_id"] = intent_id
    return Event(
        signal="manage.recycle.requested",
        payload=payload,
        role="observation",
        scope="node",
    )


@event_factory
def ManageRecycleDrainAttempted(  # noqa: N802
    *,
    intent_id: str,
    service: str = _SERVICE,
    idle_s: float,
) -> Event:
    """Drain-gated restart is armed; force is not yet in play."""
    return Event(
        signal="manage.recycle.drain_attempted",
        payload={
            "intent_id": intent_id,
            "service": service,
            "idle_s": idle_s,
        },
        role="observation",
        scope="node",
    )


@event_factory
def ManageRecycleEscalated(  # noqa: N802
    *,
    intent_id: str,
    service: str = _SERVICE,
    idle_s: float,
    active_count: int,
    stuck_ops: list[dict[str, Any]] | None = None,
) -> Event:
    """Idle-on-no-progress gate tripped; escalate to the existing force kill."""
    payload: dict[str, Any] = {
        "intent_id": intent_id,
        "service": service,
        "idle_s": idle_s,
        "active_count": active_count,
    }
    if stuck_ops is not None:
        payload["stuck_ops"] = stuck_ops
    return Event(
        signal="manage.recycle.escalated",
        payload=payload,
        role="observation",
        scope="node",
    )


@event_factory
def ManageRecycleCompleted(  # noqa: N802
    *,
    intent_id: str,
    service: str = _SERVICE,
    escalated: bool,
    duration_s: float,
) -> Event:
    """Recycle reached a process restart; ``escalated`` names the force stage."""
    return Event(
        signal="manage.recycle.completed",
        payload={
            "intent_id": intent_id,
            "service": service,
            "escalated": escalated,
            "duration_s": round(duration_s, 1),
        },
        role="observation",
        scope="node",
    )


__all__ = [
    "ManageRecycleCompleted",
    "ManageRecycleDrainAttempted",
    "ManageRecycleEscalated",
    "ManageRecycleRequested",
]
