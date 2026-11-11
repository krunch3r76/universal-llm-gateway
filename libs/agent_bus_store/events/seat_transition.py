"""Advisory observation when a shadow seat ledger row is appended."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusThreadSeatTransition(  # noqa: N802
    thread_id: str,
    epoch: int,
    transition: str,
    state: str,
    chat_id: str | None,
    actor: str,
    request_id: str,
) -> Event:
    """Signal: mcp.agentbus.thread.seat.transition"""
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "epoch": epoch,
        "transition": transition,
        "state": state,
        "chat_id": chat_id,
        "actor": actor,
        "request_id": request_id,
    }
    return Event(
        signal="mcp.agentbus.thread.seat.transition",
        payload=payload,
        role="observation",
    )


def emit_seat_transition(
    *,
    thread_id: str,
    epoch: int,
    transition: str,
    state: str,
    chat_id: str | None,
    actor: str,
    request_id: str,
) -> None:
    """Publish after a successful ``transition_seat`` insert."""
    event = AgentBusThreadSeatTransition(
        thread_id=thread_id,
        epoch=epoch,
        transition=transition,
        state=state,
        chat_id=chat_id,
        actor=actor,
        request_id=request_id,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")
