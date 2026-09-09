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
    cause: str = "no_jsonl",
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
            "cause": cause,
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
    scope: str = "last_session",
    tools: str = "none",
    include_extras: bool = False,
    index_count: int = 0,
    codec_counts: dict[str, int] | None = None,
    surfaces: list[str] | None = None,
    tools_available: bool = False,
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
            "scope": scope,
            "tools": tools,
            "include_extras": include_extras,
            "index_count": index_count,
            "codec_counts": codec_counts or {},
            "surfaces": surfaces or [],
            "tools_available": tools_available,
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


@event_factory
def transcript_discover_filtered(
    *,
    reason: str,
    thread_id: str,
    transcript_id: str,
) -> Event:
    ev = Event(
        signal="cortex.transcript.discover.filtered",
        role="observation",
        scope="global",
        payload={
            "reason": reason,
            "thread_id": thread_id,
            "transcript_id": transcript_id,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def transcript_seal_refused_not_lane_window(
    *,
    thread_id: str,
    conversation_uuid: str,
    binding: str,
) -> Event:
    ev = Event(
        signal="cortex.transcript.seal.refused_not_lane_window",
        role="observation",
        scope="global",
        payload={
            "thread_id": thread_id,
            "conversation_uuid": conversation_uuid,
            "binding": binding,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def transcript_sealed_messages(
    *,
    session_id: str,
    thread_id: str | None = None,
    transcript_id: str | None = None,
    surface: str,
    verbatim_codec: str,
    prior_codec: str | None = None,
    turn_count: int,
    messages_sha256: str,
    extended: bool = False,
    already_closed: bool = False,
) -> Event:
    payload: dict[str, object] = {
        "session_id": session_id,
        "surface": surface,
        "verbatim_codec": verbatim_codec,
        "turn_count": turn_count,
        "messages_sha256": messages_sha256,
        "extended": extended,
        "already_closed": already_closed,
    }
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if transcript_id is not None:
        payload["transcript_id"] = transcript_id
    if prior_codec is not None:
        payload["prior_codec"] = prior_codec
    ev = Event(
        signal="cortex.transcript.sealed_messages",
        role="observation",
        scope="global",
        payload=payload,
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def transcript_seal_verbatim_diverged(
    *,
    session_id: str,
    transcript_id: str | None,
    mode: str,
    codec: str,
    sealed_turns: int,
    live_turns: int,
    first_divergent_index: int,
) -> Event:
    ev = Event(
        signal="cortex.transcript.seal.verbatim_diverged",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "transcript_id": transcript_id,
            "mode": mode,
            "codec": codec,
            "sealed_turns": sealed_turns,
            "live_turns": live_turns,
            "first_divergent_index": first_divergent_index,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def transcript_seal_codec_diverged(
    *,
    session_id: str,
    transcript_id: str | None,
    prior_codec: str,
    reason: str,
) -> Event:
    ev = Event(
        signal="cortex.transcript.seal.codec_diverged",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "transcript_id": transcript_id,
            "prior_codec": prior_codec,
            "reason": reason,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def transcript_harvested(
    *,
    thread_id: str,
    discovered: int,
    sealed: int,
    deferred: int,
    refused: int,
) -> Event:
    ev = Event(
        signal="cortex.transcript.harvested",
        role="observation",
        scope="global",
        payload={
            "thread_id": thread_id,
            "discovered": discovered,
            "sealed": sealed,
            "deferred": deferred,
            "refused": refused,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def transcript_legacy_md_read(
    *,
    session_id: str,
    sentinel_count: int,
    marker_count: int,
    user_marker_hits: int,
) -> Event:
    ev = Event(
        signal="cortex.transcript.legacy_md_read",
        role="observation",
        scope="global",
        payload={
            "session_id": session_id,
            "sentinel_count": sentinel_count,
            "marker_count": marker_count,
            "user_marker_hits": user_marker_hits,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def agent_bus_tape_harvest_rendered(
    *,
    thread_id: str,
    discovered: int,
    sealed: int,
    deferred: int,
) -> Event:
    ev = Event(
        signal="agent_bus.tape.harvest_rendered",
        role="observation",
        scope="global",
        payload={
            "thread_id": thread_id,
            "discovered": discovered,
            "sealed": sealed,
            "deferred": deferred,
        },
    )
    record(ev.signal, **ev.payload)
    return ev
