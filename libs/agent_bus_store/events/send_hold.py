"""Observation event for new-thread send hold timing (mint + prepare + insert)."""

from __future__ import annotations

from typing import Any, Literal

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish

RouteName = Literal["send", "with_turn"]


@event_factory
def AgentBusSendHoldMeasured(  # noqa: N802
    thread: str,
    route: RouteName,
    is_checkpoint: bool,
    body_chars: int,
    spilled: bool,
    mint_hold_ms: float,
    prepare_ms: float,
    insert_call_ms: float,
) -> Event:
    """Signal: mcp.agentbus.send.hold.measured"""
    payload: dict[str, Any] = {
        "thread": thread,
        "route": route,
        "is_checkpoint": is_checkpoint,
        "body_chars": body_chars,
        "spilled": spilled,
        "mint_hold_ms": mint_hold_ms,
        "prepare_ms": prepare_ms,
        "insert_call_ms": insert_call_ms,
    }
    return Event(
        signal="mcp.agentbus.send.hold.measured",
        payload=payload,
        role="observation",
    )


def emit_send_hold_measured(
    *,
    thread: str,
    route: RouteName,
    is_checkpoint: bool,
    body_chars: int,
    spilled: bool,
    mint_hold_ms: float,
    prepare_ms: float,
    insert_call_ms: float,
) -> None:
    """Publish ``mcp.agentbus.send.hold.measured`` after a successful new-thread send."""
    event = AgentBusSendHoldMeasured(
        thread=thread,
        route=route,
        is_checkpoint=is_checkpoint,
        body_chars=body_chars,
        spilled=spilled,
        mint_hold_ms=mint_hold_ms,
        prepare_ms=prepare_ms,
        insert_call_ms=insert_call_ms,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")
