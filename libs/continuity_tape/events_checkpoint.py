"""Continuity checkpoint observation events (Phase 4b pipeline surfaces)."""

from __future__ import annotations

from cortex_store.event_publisher import record
from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def stargate_continuity_checkpoint_admitted(
    *,
    execution_id: str,
    thread: str,
    surface: str,
    from_agent: str,
) -> Event:
    ev = Event(
        signal="stargate.continuity.checkpoint.admitted",
        role="observation",
        scope="global",
        payload={
            "execution_id": execution_id,
            "thread": thread,
            "surface": surface,
            "from_agent": from_agent,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def stargate_continuity_checkpoint_sealed(
    *,
    execution_id: str,
    thread: str,
    surface: str,
    from_agent: str,
    transcript_id: str,
    turns_at_cp: int,
    session_id: str | None = None,
) -> Event:
    payload: dict[str, object] = {
        "execution_id": execution_id,
        "thread": thread,
        "surface": surface,
        "from_agent": from_agent,
        "transcript_id": transcript_id,
        "turns_at_cp": turns_at_cp,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    ev = Event(
        signal="stargate.continuity.checkpoint.sealed",
        role="observation",
        scope="global",
        payload=payload,
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def stargate_continuity_checkpoint_card_patched(
    *,
    execution_id: str,
    thread: str,
    surface: str,
    from_agent: str,
    card_uri: str,
    executor: str,
) -> Event:
    ev = Event(
        signal="stargate.continuity.checkpoint.card_patched",
        role="observation",
        scope="global",
        payload={
            "execution_id": execution_id,
            "thread": thread,
            "surface": surface,
            "from_agent": from_agent,
            "card_uri": card_uri,
            "executor": executor,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def stargate_continuity_checkpoint_posted(
    *,
    execution_id: str,
    thread: str,
    surface: str,
    from_agent: str,
    bus_turn: int,
) -> Event:
    ev = Event(
        signal="stargate.continuity.checkpoint.posted",
        role="observation",
        scope="global",
        payload={
            "execution_id": execution_id,
            "thread": thread,
            "surface": surface,
            "from_agent": from_agent,
            "bus_turn": bus_turn,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def stargate_continuity_checkpoint_failed(
    *,
    execution_id: str,
    thread: str,
    surface: str,
    from_agent: str,
    stage: str,
    code: str,
) -> Event:
    ev = Event(
        signal="stargate.continuity.checkpoint.failed",
        role="observation",
        scope="global",
        payload={
            "execution_id": execution_id,
            "thread": thread,
            "surface": surface,
            "from_agent": from_agent,
            "stage": stage,
            "code": code,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def mcp_continuity_checkpoint_requested(
    *,
    surface: str,
    thread: str,
) -> Event:
    ev = Event(
        signal="mcp.continuity.checkpoint.requested",
        role="observation",
        scope="global",
        payload={"surface": surface, "thread": thread},
    )
    record(ev.signal, **ev.payload)
    return ev


__all__ = [
    "mcp_continuity_checkpoint_requested",
    "stargate_continuity_checkpoint_admitted",
    "stargate_continuity_checkpoint_card_patched",
    "stargate_continuity_checkpoint_failed",
    "stargate_continuity_checkpoint_posted",
    "stargate_continuity_checkpoint_sealed",
]
