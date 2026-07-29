"""Emit helper for store-layer turn-created coordination signal.

``mcp.agentbus.turn.created`` fires on every successful turn insert in the
agent-bus SQLite store (all transport paths), so WakeHub can subscribe without
relying on the MCP tool layer.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusTurnCreated(  # noqa: N802
    thread: str,
    turn_id: int,
    turn_number: int,
    from_agent: str,
    to_agent: str,
    subject: str,
    created_at: str,
) -> Event:
    """Signal: mcp.agentbus.turn.created"""
    payload: dict[str, Any] = {
        "thread": thread,
        "turn_id": turn_id,
        "turn_number": turn_number,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "subject": subject,
        "created_at": created_at,
    }
    return Event(
        signal="mcp.agentbus.turn.created",
        payload=payload,
        role="coordination",
    )


def emit_turn_created(
    *,
    thread: str,
    turn_id: int,
    turn_number: int,
    from_agent: str,
    to_agent: str,
    subject: str,
    created_at: str,
) -> None:
    """Publish ``mcp.agentbus.turn.created`` after a store turn insert."""
    event = AgentBusTurnCreated(
        thread=thread,
        turn_id=turn_id,
        turn_number=turn_number,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        created_at=created_at,
    )
    _publish(event.signal, event.payload, role=event.role or "coordination")
