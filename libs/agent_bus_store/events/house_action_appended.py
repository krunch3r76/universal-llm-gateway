"""Observation event for committed house action journal appends."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusHouseActionAppended(  # noqa: N802
    root_thread_id: str,
    kind: str,
    actor: str,
    action_why: str,
    journal_id: int,
) -> Event:
    """Signal: mcp.agentbus.house.action.appended"""
    payload: dict[str, Any] = {
        "root_thread_id": root_thread_id,
        "kind": kind,
        "actor": actor,
        "action_why": action_why,
        "journal_id": journal_id,
    }
    return Event(
        signal="mcp.agentbus.house.action.appended",
        payload=payload,
        role="observation",
    )


def emit_house_action_appended(**kwargs: Any) -> None:
    """Publish after a journal row commits."""
    event = AgentBusHouseActionAppended(**kwargs)
    _publish(event.signal, event.payload, role=event.role or "observation")
