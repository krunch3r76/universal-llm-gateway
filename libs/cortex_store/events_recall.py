"""Recall card lifecycle events — graph.recall.* namespace."""

from __future__ import annotations

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory

from .event_publisher import record


@event_factory
def graph_recall_card_served(
    *,
    mode: str,
    resolved_count: int,
    nulls: list[str],
) -> Event:
    """graph.recall.card_served — recall card returned on a successful route."""
    ev = Event(
        signal="graph.recall.card_served",
        role="observation",
        scope="global",
        payload={
            "mode": mode,
            "resolved_count": resolved_count,
            "nulls": nulls,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def graph_recall_resolver_miss(
    *,
    mode: str,
    q_present: bool,
    seed_count: int,
) -> Event:
    """graph.recall.resolver_miss — no hub resolved and no candidates returned."""
    ev = Event(
        signal="graph.recall.resolver_miss",
        role="observation",
        scope="global",
        payload={
            "mode": mode,
            "q_present": q_present,
            "seed_count": seed_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def graph_recall_burst_not_covered(
    *,
    mode: str,
    hub_ids: list[str],
) -> Event:
    """graph.recall.burst_not_covered — burst plug-in skipped or vocab miss."""
    ev = Event(
        signal="graph.recall.burst_not_covered",
        role="observation",
        scope="global",
        payload={
            "mode": mode,
            "hub_ids": hub_ids,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def graph_recall_escalated_to_delegate(
    *,
    mode: str,
    reason: str,
) -> Event:
    """graph.recall.escalated_to_delegate — advisory _next points at delegate."""
    ev = Event(
        signal="graph.recall.escalated_to_delegate",
        role="observation",
        scope="global",
        payload={
            "mode": mode,
            "reason": reason,
        },
    )
    record(ev.signal, **ev.payload)
    return ev
