"""Continuity tape observation events (Phase 0 relay surfaces)."""

from __future__ import annotations

from cortex_store.event_publisher import record
from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def stargate_continuity_tape_read_served(
    *,
    thread: str,
    scope: str,
    tools: str,
    include_extras: bool,
    message_count: int,
    index_count: int,
    payload_bytes: int,
    truncated: bool,
    caller_agent: str,
    door: str,
    execution_id: str | None = None,
    pour_bytes: int | None = None,
    sidecar_slug: str | None = None,
) -> Event:
    payload: dict[str, object] = {
        "thread": thread,
        "scope": scope,
        "tools": tools,
        "include_extras": include_extras,
        "message_count": message_count,
        "index_count": index_count,
        "payload_bytes": payload_bytes,
        "truncated": truncated,
        "caller_agent": caller_agent,
        "door": door,
    }
    if execution_id is not None:
        payload["execution_id"] = execution_id
    if pour_bytes is not None:
        payload["pour_bytes"] = pour_bytes
    if sidecar_slug is not None:
        payload["sidecar_slug"] = sidecar_slug
    ev = Event(
        signal="stargate.continuity.tape_read.served",
        role="observation",
        scope="global",
        payload=payload,
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def mcp_continuity_tape_read_requested(
    *,
    thread: str,
    scope: str,
    door: str,
) -> Event:
    ev = Event(
        signal="mcp.continuity.tape_read.requested",
        role="observation",
        scope="global",
        payload={"thread": thread, "scope": scope, "door": door},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def continuity_messages_extracted(
    *,
    surface: str,
    transcript_id: str,
    message_count: int,
    turn_count: int,
    user_turns: int,
    tools: str,
    truncated: bool,
    source: str,
    chat_url_sha: str | None = None,
) -> Event:
    payload: dict[str, object] = {
        "surface": surface,
        "transcript_id": transcript_id,
        "message_count": message_count,
        "turn_count": turn_count,
        "user_turns": user_turns,
        "tools": tools,
        "truncated": truncated,
        "source": source,
    }
    if chat_url_sha is not None:
        payload["chat_url_sha"] = chat_url_sha
    ev = Event(
        signal="continuity.messages.extracted",
        role="observation",
        scope="global",
        payload=payload,
    )
    record(ev.signal, **ev.payload)
    return ev


__all__ = [
    "stargate_continuity_tape_read_served",
    "mcp_continuity_tape_read_requested",
    "continuity_messages_extracted",
]
