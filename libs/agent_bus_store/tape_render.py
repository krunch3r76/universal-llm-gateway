"""CP-cell partition and messages+extras tape render on read."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from cortex_store.session_close_successor_hop import lookup_sealed_journal

from .checkpoint_windows_render import list_checkpoint_turns
from .db.connection import connect

_WINDOW_LINE_RE = re.compile(
    r"transcript_id=(?P<uuid>[0-9a-f-]+)\s*·\s*turns@cp=(?P<turns>\d+)",
    re.IGNORECASE,
)
_WHOLE_BOUNDARY_RE = re.compile(
    r"boundary=window_whole",
    re.IGNORECASE,
)
_DEFAULT_BUDGET_BYTES = 512_000


@dataclass(frozen=True)
class TapeSegment:
    session_id: str
    transcript_id: str
    turn_count: int
    verbatim_sha256: str
    conversation_uuid: str | None
    binding: str
    boundary: str | None = None
    post_lid_turns: int = 0


def _verbatim_sha256(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _split_verbatim_layer(full_md: str) -> str:
    marker = "\n## Session Summary"
    idx = full_md.find(marker)
    if idx == -1:
        return full_md
    return full_md[:idx]


def _load_sealed_segment(session_id: str) -> TapeSegment | None:
    row = lookup_sealed_journal(session_id)
    if row is None:
        return None
    from cortex_store.db import cortex_conn

    conn = cortex_conn()
    try:
        journal = conn.execute(
            "SELECT file_path, conversation_uuid, closed_by FROM session_journals "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if journal is None or not journal["file_path"]:
        return None
    from cortex_store.dispatch_ops._shared import _FILES_ROOT

    path = _FILES_ROOT / journal["file_path"]
    if not path.is_file():
        return None
    full = path.read_text(encoding="utf-8")
    verbatim = _split_verbatim_layer(full)
    turn_count = sum(1 for line in verbatim.splitlines() if line.startswith("## Turn"))
    uuid = journal["conversation_uuid"]
    binding = "dominant_write"
    if journal["closed_by"] == "succession":
        binding = "sole"
    return TapeSegment(
        session_id=session_id,
        transcript_id=uuid or session_id,
        turn_count=turn_count,
        verbatim_sha256=_verbatim_sha256(verbatim),
        conversation_uuid=uuid,
        binding=binding,
        boundary="window_whole" if turn_count == 0 else None,
    )


def _parse_window_lines(body: str) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for line in body.splitlines():
        match = _WINDOW_LINE_RE.search(line)
        if match:
            anchors.append(
                {
                    "transcript_id": match.group("uuid"),
                    "turns_at_cp": int(match.group("turns")),
                }
            )
        elif _WHOLE_BOUNDARY_RE.search(line):
            anchors.append({"boundary": "window_whole"})
    return anchors


def _cells_for_lane(*, thread_id: str) -> list[dict[str, Any]]:
    cps = list_checkpoint_turns(thread_id=thread_id)
    cells: list[dict[str, Any]] = []
    for cp in cps:
        with connect() as conn:
            row = conn.execute(
                "SELECT body FROM turns WHERE thread = ? AND turn_number = ?",
                (thread_id, cp.turn_number),
            ).fetchone()
        body = str(row["body"]) if row else ""
        for anchor in _parse_window_lines(body):
            cells.append(
                {
                    "cp_ordinal": cp.cp_ordinal,
                    "cp_turn": cp.turn_number,
                    "transcript_id": anchor.get("transcript_id"),
                    "turns_at_cp": anchor.get("turns_at_cp"),
                    "boundary": anchor.get("boundary"),
                }
            )
    return cells


def render_tape(
    *,
    thread_id: str,
    budget_bytes: int = _DEFAULT_BUDGET_BYTES,
    seal_first: bool = False,
) -> dict[str, Any]:
    """Render messages+extras dump for a continuity lane (read-only)."""
    del seal_first  # seal-then-render is orchestrated by the caller/resume path
    from cortex_store.db import cortex_conn, decode_row

    json_fields = frozenset({"domains", "decisions", "open_items", "entity_ids"})
    agent_bus_ref = f"agent-bus:{thread_id}"
    with cortex_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM session_journals WHERE entity_ids IS NOT NULL "
            "AND ("
            "  EXISTS (SELECT 1 FROM json_each(entity_ids) WHERE value = ?) "
            "  OR EXISTS (SELECT 1 FROM json_each(entity_ids) WHERE value = ?)"
            ") ORDER BY id ASC",
            (agent_bus_ref, thread_id),
        ).fetchall()
    journals = [decode_row(row, json_fields) for row in rows]

    segments: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for journal in journals:
        sid = journal.get("session_id")
        if not sid:
            continue
        if journal.get("file_path") is None:
            excluded.append({"session_id": sid, "reason": "segment_unavailable", "depth": "light"})
            continue
        seg = _load_sealed_segment(str(sid))
        if seg is None:
            excluded.append({"session_id": sid, "reason": "segment_unavailable", "depth": "light"})
            continue
        segments.append(
            {
                "session_id": seg.session_id,
                "transcript_id": seg.transcript_id,
                "turn_count": seg.turn_count,
                "verbatim_sha256": seg.verbatim_sha256,
                "conversation_uuid": seg.conversation_uuid,
                "binding": seg.binding,
                "boundary": seg.boundary or (
                    "window_whole" if seg.turn_count >= 0 else None
                ),
            }
        )

    cells = _cells_for_lane(thread_id=thread_id)
    messages: list[dict[str, Any]] = []
    for seg in segments:
        sid = seg["session_id"]
        from cortex_store.dispatch_ops._shared import _FILES_ROOT

        journal = next(j for j in journals if j.get("session_id") == sid)
        file_path = journal.get("file_path")
        if not file_path:
            continue
        full = (_FILES_ROOT / file_path).read_text(encoding="utf-8")
        verbatim = _split_verbatim_layer(full)
        for idx, line in enumerate(verbatim.splitlines(), start=1):
            if line.startswith("### User"):
                continue
            if line.startswith("### "):
                role = "assistant"
                messages.append(
                    {
                        "role": role,
                        "content": line,
                        "transcript_id": seg["transcript_id"],
                        "session_id": sid,
                        "turn_index": idx,
                        "window_whole": seg.get("boundary") == "window_whole",
                    }
                )

    payload_bytes = len(json.dumps(messages).encode("utf-8"))
    truncated = payload_bytes > budget_bytes
    if truncated:
        messages = messages[-max(1, budget_bytes // 256) :]

    from cortex_store.events_tape import agent_bus_tape_rendered

    agent_bus_tape_rendered(
        thread_id=thread_id,
        segment_count=len(segments),
        turn_count=sum(s.get("turn_count", 0) for s in segments),
        truncated=truncated,
    )
    return {
        "thread_id": thread_id,
        "segments": segments,
        "cells": cells,
        "messages": messages,
        "excluded": excluded,
        "truncated": truncated,
        "turn_count": sum(s.get("turn_count", 0) for s in segments),
        "segment_count": len(segments),
    }


__all__ = ["render_tape", "TapeSegment"]
