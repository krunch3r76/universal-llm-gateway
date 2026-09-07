"""Speech-tape and succession harvest events."""

from __future__ import annotations

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory

from .event_publisher import record


@event_factory
def session_close_succession_structural_filled(
    *,
    session_id: str,
    agent: str,
    journal_row_id: int,
    extended: bool = False,
    reason: str = "SPLICE",
) -> Event:
    ev = Event(
        signal="cortex.session_close.succession_structural_filled",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "agent": agent,
            "journal_row_id": journal_row_id,
            "extended": extended,
            "reason": reason,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def session_close_root_window_depth_upgraded(
    *,
    session_id: str,
    thread_id: str,
    prior_depth: str,
    new_depth: str,
) -> Event:
    ev = Event(
        signal="cortex.session_close.root_window_depth_upgraded",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "thread_id": thread_id,
            "prior_depth": prior_depth,
            "new_depth": new_depth,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def transcript_sealed_by_succession(
    *,
    session_id: str,
    thread_id: str,
    conversation_uuid: str | None,
    turn_count: int,
) -> Event:
    ev = Event(
        signal="cortex.transcript.sealed_by_succession",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "thread_id": thread_id,
            "conversation_uuid": conversation_uuid,
            "turn_count": turn_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def agent_bus_tape_rendered(
    *,
    thread_id: str,
    segment_count: int,
    turn_count: int,
    truncated: bool,
) -> Event:
    ev = Event(
        signal="agent_bus.tape.rendered",
        role="observation",
        scope="global",
        payload={
            "thread_id": thread_id,
            "segment_count": segment_count,
            "turn_count": turn_count,
            "truncated": truncated,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def agent_bus_tape_segment_unavailable(
    *,
    thread_id: str,
    session_id: str,
    depth: str,
) -> Event:
    ev = Event(
        signal="agent_bus.tape.segment_unavailable",
        role="observation",
        scope="global",
        payload={
            "thread_id": thread_id,
            "session_id": session_id,
            "depth": depth,
        },
    )
    record(ev.signal, **ev.payload)
    return ev
