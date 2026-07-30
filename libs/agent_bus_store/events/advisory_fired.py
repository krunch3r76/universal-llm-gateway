"""Emit helper for kind-scoped briefing advisory (I6)."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusAdvisoryFired(  # noqa: N802
    *,
    advisory: str,
    turn_kind: str,
    chars: int,
    suppressed_by_profile: bool,
    thread: str,
    subject: str,
) -> Event:
    """Signal: mcp.agentbus.advisory.fired"""
    payload: dict[str, Any] = {
        "advisory": advisory,
        "turn_kind": turn_kind,
        "chars": chars,
        "suppressed_by_profile": suppressed_by_profile,
        "thread": thread,
        "subject": subject[:80],
    }
    return Event(
        signal="mcp.agentbus.advisory.fired",
        payload=payload,
        role="observation",
    )


def emit_advisory_fired(
    *,
    advisory: str,
    turn_kind: str,
    chars: int,
    suppressed_by_profile: bool,
    thread: str,
    subject: str,
) -> None:
    """Publish ``mcp.agentbus.advisory.fired`` when a profile-scoped advisory fires."""
    event = AgentBusAdvisoryFired(
        advisory=advisory,
        turn_kind=turn_kind,
        chars=chars,
        suppressed_by_profile=suppressed_by_profile,
        thread=thread,
        subject=subject,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")


__all__ = ["emit_advisory_fired"]
