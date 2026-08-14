"""Emit helper for successful lane parentage association appends."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusLaneBound(  # noqa: N802
    thread_id: str,
    parent_thread_id: str,
    lane_role: str,
    association_id: int,
    prior_association_id: int | None,
    bound_by: str | None,
) -> Event:
    """Signal: mcp.agentbus.lane.bound"""
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "parent_thread_id": parent_thread_id,
        "lane_role": lane_role,
        "association_id": association_id,
        "prior_association_id": prior_association_id,
        "bound_by": bound_by,
    }
    return Event(
        signal="mcp.agentbus.lane.bound",
        payload=payload,
        role="observation",
    )


def emit_lane_bound(
    *,
    thread_id: str,
    parent_thread_id: str,
    lane_role: str,
    association_id: int,
    prior_association_id: int | None,
    bound_by: str | None,
) -> None:
    """Publish advisory event after a successful lane association append."""
    event = AgentBusLaneBound(
        thread_id=thread_id,
        parent_thread_id=parent_thread_id,
        lane_role=lane_role,
        association_id=association_id,
        prior_association_id=prior_association_id,
        bound_by=bound_by,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")
