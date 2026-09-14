"""Emit helper when auto-mint of ``thread:{id}`` fails after bus thread writes."""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusThreadEntityMintFailed(  # noqa: N802
    thread: str,
    entity_id: str,
    error: str,
    *,
    phase: str,
) -> Event:
    """Signal: mcp.agentbus.thread.entity.mint.failed"""
    return Event(
        signal="mcp.agentbus.thread.entity.mint.failed",
        payload={
            "thread": thread,
            "entity_id": entity_id,
            "error": error,
            "phase": phase,
        },
        role="observation",
    )


def emit_thread_entity_mint_failed(
    *,
    thread: str,
    entity_id: str,
    error: str,
    phase: str,
) -> None:
    """Publish ``mcp.agentbus.thread.entity.mint.failed`` (best-effort side effect)."""
    event = AgentBusThreadEntityMintFailed(
        thread=thread,
        entity_id=entity_id,
        error=error,
        phase=phase,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")


__all__ = ["emit_thread_entity_mint_failed"]
