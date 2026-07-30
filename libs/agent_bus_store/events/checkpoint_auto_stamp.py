"""Emit helper for structural CHECKPOINT auto-stamp observation."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusCheckpointAutoStamp(  # noqa: N802
    thread: str,
    turn_number: int,
    subject: str,
) -> Event:
    """Signal: mcp.agentbus.checkpoint.auto_stamp"""
    payload: dict[str, Any] = {
        "thread": thread,
        "turn_number": turn_number,
        "subject": subject[:80],
    }
    return Event(
        signal="mcp.agentbus.checkpoint.auto_stamp",
        payload=payload,
        role="observation",
    )


def emit_checkpoint_auto_stamp(
    *,
    thread: str,
    turn_number: int,
    subject: str,
) -> None:
    """Publish ``mcp.agentbus.checkpoint.auto_stamp`` after stamping ``role:root``."""
    event = AgentBusCheckpointAutoStamp(
        thread=thread,
        turn_number=turn_number,
        subject=subject,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")


__all__ = ["emit_checkpoint_auto_stamp"]
