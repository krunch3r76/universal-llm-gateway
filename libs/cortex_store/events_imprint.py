"""Imprint propose lifecycle events — graph.imprint.* namespace."""

from __future__ import annotations

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory

from .event_publisher import record


@event_factory
def graph_imprint_received(
    *,
    statement_count: int,
    context: str,
) -> Event:
    """graph.imprint.received — patch received and passed initial envelope check."""
    ev = Event(
        signal="graph.imprint.received",
        role="observation",
        scope="global",
        payload={
            "statement_count": statement_count,
            "context": context,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def graph_imprint_proposed(
    *,
    statement_count: int,
    op_plan_count: int,
    candidate_count: int,
) -> Event:
    """graph.imprint.proposed — op plan produced for a shape-valid patch."""
    ev = Event(
        signal="graph.imprint.proposed",
        role="observation",
        scope="global",
        payload={
            "statement_count": statement_count,
            "op_plan_count": op_plan_count,
            "candidate_count": candidate_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def graph_imprint_rejected(
    *,
    statement_count: int,
    reject_count: int,
    reject_codes: list[str],
) -> Event:
    """graph.imprint.rejected — vocabulary/shape/refused-op reject."""
    ev = Event(
        signal="graph.imprint.rejected",
        role="observation",
        scope="global",
        payload={
            "statement_count": statement_count,
            "reject_count": reject_count,
            "reject_codes": reject_codes,
        },
    )
    record(ev.signal, **ev.payload)
    return ev
