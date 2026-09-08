"""Collect explicit transcript uuids from CP Window: anchors on a lane."""

from __future__ import annotations

import re

_WINDOW_LINE_RE = re.compile(
    r"transcript_id=(?P<uuid>[0-9a-f-]+)\s*·\s*turns@cp=(?P<turns>\d+)",
    re.IGNORECASE,
)


def window_anchors_from_text(text: str) -> tuple[tuple[str, int], ...]:
    """Parse ``Window: transcript_id=… · turns@cp=N`` anchors from CP residue."""
    anchors: list[tuple[str, int]] = []
    for line in text.splitlines():
        match = _WINDOW_LINE_RE.search(line)
        if match:
            anchors.append((str(match.group("uuid")), int(match.group("turns"))))
    return tuple(anchors)


def explicit_uuids_for_lane(thread_id: str, extra: set[str] | None = None) -> set[str]:
    """Return CP-anchored transcript ids for *thread_id* plus *extra*."""
    explicit = {x.strip() for x in (extra or set()) if x and str(x).strip()}
    from agent_bus_store.checkpoint_windows_render import list_checkpoint_turns
    from agent_bus_store.db.connection import connect

    for cp in list_checkpoint_turns(thread_id=thread_id):
        with connect() as conn:
            row = conn.execute(
                "SELECT body FROM turns WHERE thread = ? AND turn_number = ?",
                (thread_id, cp.turn_number),
            ).fetchone()
        body = str(row["body"]) if row else ""
        for line in body.splitlines():
            match = _WINDOW_LINE_RE.search(line)
            if match:
                explicit.add(str(match.group("uuid")))
    return explicit


__all__ = ["explicit_uuids_for_lane", "window_anchors_from_text"]
