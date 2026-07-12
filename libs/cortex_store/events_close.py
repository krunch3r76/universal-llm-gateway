"""Close-verb lifecycle events — close.* namespace."""

from __future__ import annotations

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory

from .event_publisher import record


@event_factory
def close_draft_opened(*, session_id: str, agent: str, revision: int) -> Event:
    ev = Event(
        signal="close.draft.opened",
        role="observation",
        scope="global",
        payload={"session_id": session_id, "agent": agent, "revision": revision},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def close_draft_updated(*, session_id: str, agent: str, revision: int) -> Event:
    ev = Event(
        signal="close.draft.updated",
        role="observation",
        scope="global",
        payload={"session_id": session_id, "agent": agent, "revision": revision},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def close_check_completed(
    *,
    session_id: str,
    agent: str,
    revision: int,
    status: str,
    gap_count: int,
) -> Event:
    ev = Event(
        signal="close.check.completed",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "agent": agent,
            "revision": revision,
            "status": status,
            "gap_count": gap_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def close_commit_completed(
    *,
    session_id: str,
    agent: str,
    journal_row_id: int,
    transcript_depth: str,
) -> Event:
    ev = Event(
        signal="close.commit.completed",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "agent": agent,
            "journal_row_id": journal_row_id,
            "transcript_depth": transcript_depth,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def close_handoff_upserted(*, session_id: str, journal_row_id: int) -> Event:
    ev = Event(
        signal="close.handoff.upserted",
        role="observation",
        scope="global",
        payload={"session_id": session_id, "journal_row_id": journal_row_id},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def close_draft_reflections_flushed(
    *,
    session_id: str,
    agent: str,
    reflection_count: int,
) -> Event:
    ev = Event(
        signal="close.draft.reflections.flushed",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "agent": agent,
            "reflection_count": reflection_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev
