"""Emit helper for store-layer turn body over-briefing observation signal."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusTurnBodyOverBriefing(  # noqa: N802
    thread: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    body_chars: int,
    target_chars: int,
) -> Event:
    """Signal: mcp.agentbus.turn.body_over_briefing"""
    payload: dict[str, Any] = {
        "thread": thread,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "subject": subject[:80],
        "body_chars": body_chars,
        "target_chars": target_chars,
    }
    return Event(
        signal="mcp.agentbus.turn.body_over_briefing",
        payload=payload,
        role="observation",
    )


def emit_turn_body_over_briefing(
    *,
    thread: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    body_chars: int,
    target_chars: int,
) -> None:
    """Publish ``mcp.agentbus.turn.body_over_briefing`` after an advisory fires."""
    event = AgentBusTurnBodyOverBriefing(
        thread=thread,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        body_chars=body_chars,
        target_chars=target_chars,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")
