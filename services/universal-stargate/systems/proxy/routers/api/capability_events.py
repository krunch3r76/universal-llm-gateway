"""Observation events for the capability relay."""

from __future__ import annotations

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def capability_relay_completed(
    *,
    category: str,
    member: str,
    method: str,
    status: int,
    duration_ms: float,
) -> Event:
    """Stargate relayed a capability call."""
    return Event(
        signal="capability.relay.completed",
        role="observation",
        scope="node",
        payload={
            "category": category,
            "member": member,
            "method": method,
            "status": status,
            "duration_ms": duration_ms,
        },
    )


@event_factory
def capability_relay_failed(
    *,
    category: str,
    member: str,
    upstream: str,
    error: str,
) -> Event:
    """The capability upstream was unreachable or timed out."""
    return Event(
        signal="capability.relay.failed",
        role="observation",
        scope="node",
        payload={
            "category": category,
            "member": member,
            "upstream": upstream,
            "error": error,
        },
    )


@event_factory
def capability_vocabulary_rejected(
    *,
    category: str,
    reason: str,
) -> Event:
    """A vocabulary entry was skipped on load or reload."""
    return Event(
        signal="capability.vocabulary.rejected",
        role="observation",
        scope="node",
        payload={"category": category, "reason": reason},
    )
