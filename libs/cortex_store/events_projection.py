"""Transcript projection lifecycle events — cortex.transcript.* namespace."""

from __future__ import annotations

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory

from .event_publisher import record


@event_factory
def cortex_transcript_projected(
    *,
    thread: str,
    windows_scanned: int,
    parsed: int,
    windows_changed: int,
    cells_added: int,
    anchor_mismatches: int,
    elapsed_ms: int,
) -> Event:
    """cortex.transcript.projected — projection run completed with writes or dry-run compute."""
    ev = Event(
        signal="cortex.transcript.projected",
        role="observation",
        scope="global",
        payload={
            "thread": thread,
            "windows_scanned": windows_scanned,
            "parsed": parsed,
            "windows_changed": windows_changed,
            "cells_added": cells_added,
            "anchor_mismatches": anchor_mismatches,
            "elapsed_ms": elapsed_ms,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_transcript_projection_refused(
    *,
    thread: str,
    code: str,
) -> Event:
    """cortex.transcript.projection_refused — op refused before any durable write."""
    ev = Event(
        signal="cortex.transcript.projection_refused",
        role="observation",
        scope="global",
        payload={"thread": thread, "code": code},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_transcript_projection_anchor_mismatch(
    *,
    thread: str,
    cp_turn: int,
    kind: str,
) -> Event:
    """cortex.transcript.projection_anchor_mismatch — one CP Window anchor mismatch detected."""
    ev = Event(
        signal="cortex.transcript.projection_anchor_mismatch",
        role="observation",
        scope="global",
        payload={"thread": thread, "cp_turn": cp_turn, "kind": kind},
    )
    record(ev.signal, **ev.payload)
    return ev


__all__ = [
    "cortex_transcript_projected",
    "cortex_transcript_projection_anchor_mismatch",
    "cortex_transcript_projection_refused",
]
