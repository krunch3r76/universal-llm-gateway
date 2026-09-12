"""CP-cell partition and messages+extras tape render on read."""

from __future__ import annotations

from typing import Any

from continuity_tape.messages import Tools, messages_sha256

from .checkpoint_windows_render import list_checkpoint_turns
from .db.connection import connect
from .tape_membership import (
    TapeSegment,
    _binding_for_journal,
    build_chain_segments,
    build_lane_segments,
    filter_lane_journals,
)
from .tape_pour import (
    _cells_for_lane,
    _filter_messages_to_cells,
    _last_session_cells,
    _window_segments,
    build_open_line,
    pour_lane_messages,
)

_DEFAULT_BUDGET_BYTES = 512_000


def _find_jsonl_for_uuid(conversation_uuid: str) -> Any | None:
    from cortex_store.transcript_assembly import _transcripts_root
    from cortex_store.transcript_session_id import jsonl_path_for_uuid

    jsonl_path = jsonl_path_for_uuid(_transcripts_root(), conversation_uuid)
    return jsonl_path if jsonl_path.is_file() else None


def _load_live_anchor_transcript(
    transcript_id: str,
) -> tuple[int, list[dict[str, Any]], str] | None:
    jsonl_path = _find_jsonl_for_uuid(transcript_id)
    if jsonl_path is None:
        return None
    from continuity_tape.extract_jsonl import extract_turns_from_jsonl
    from cortex_store.transcript_session_id import derive_session_id_from_jsonl_start

    try:
        session_id = (
            derive_session_id_from_jsonl_start(jsonl_path=jsonl_path, agent="cursor")
            or transcript_id
        )
        envelope = extract_turns_from_jsonl(
            jsonl_path, tools="marker", session_id=session_id
        )
        live_turns = envelope.meta.turn_count or max(
            (int(m.get("turn_index") or 0) for m in envelope.messages),
            default=0,
        )
    except (OSError, ValueError):
        return None
    if live_turns <= 0:
        return None
    messages = [
        {
            "role": m.get("role"),
            "content": m.get("content"),
            "transcript_id": transcript_id,
            "session_id": session_id,
            "turn_index": int(m.get("turn_index") or 0),
            "source": "cursor-jsonl",
        }
        for m in envelope.messages
        if int(m.get("turn_index") or 0) > 0
    ]
    return live_turns, messages, session_id


def live_jsonl_turn_count(transcript_id: str) -> int:
    loaded = _load_live_anchor_transcript(transcript_id)
    return loaded[0] if loaded is not None else 0


def post_lid_tail(
    *,
    conversation_uuid: str | None,
    sealed_turn_count: int,
    session_id: str,
) -> tuple[int, list[dict[str, Any]] | None]:
    if not conversation_uuid or sealed_turn_count < 0:
        return 0, None
    jsonl_path = _find_jsonl_for_uuid(str(conversation_uuid))
    if jsonl_path is None:
        return 0, None
    from continuity_tape.extract_jsonl import extract_turns_from_jsonl

    try:
        envelope = extract_turns_from_jsonl(
            jsonl_path, tools="marker", session_id=session_id
        )
        live_turns = envelope.meta.turn_count or max(
            (int(m.get("turn_index") or 0) for m in envelope.messages),
            default=0,
        )
    except (OSError, ValueError):
        return 0, None
    if live_turns <= sealed_turn_count:
        return 0, None
    tail_messages = [
        {
            "role": m.get("role"),
            "content": m.get("content"),
            "transcript_id": str(conversation_uuid),
            "session_id": session_id,
            "turn_index": int(m.get("turn_index") or 0),
            "source": "cursor-jsonl",
        }
        for m in envelope.messages
        if int(m.get("turn_index") or 0) > sealed_turn_count
    ]
    return live_turns - sealed_turn_count, tail_messages


def anchor_jsonl_messages(
    thread_id: str,
    *,
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from .tape_membership import _explicit_uuids_for_lane

    seen = {
        (
            str(msg.get("transcript_id") or ""),
            int(msg.get("turn_index") or 0),
            str(msg.get("role") or ""),
            str(msg.get("content") or ""),
        )
        for msg in existing
    }
    added: list[dict[str, Any]] = []
    for transcript_id in sorted(_explicit_uuids_for_lane(thread_id)):
        loaded = _load_live_anchor_transcript(transcript_id)
        if loaded is None:
            continue
        live_turns, live_messages, _session_id = loaded
        for msg in live_messages:
            turn_index = int(msg.get("turn_index") or 0)
            if turn_index <= 0 or turn_index > live_turns:
                continue
            key = (
                str(msg.get("transcript_id") or ""),
                turn_index,
                str(msg.get("role") or ""),
                str(msg.get("content") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            added.append({**msg, "window_whole": False})
    return added


def _build_mismatch_rows(
    *,
    cells: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    max_turn_by_tid: dict[str, int] = {}
    post_lid_by_tid: dict[str, int] = {}
    for seg in segments:
        tid = str(seg.get("transcript_id") or "")
        max_turn_by_tid[tid] = max(max_turn_by_tid.get(tid, 0), int(seg.get("turn_hi") or 0))
        post_lid_by_tid[tid] = max(post_lid_by_tid.get(tid, 0), int(seg.get("post_lid_turns") or 0))
    mismatches: list[dict[str, Any]] = []
    for cell in cells:
        if cell.get("bus_turn_id") is None:
            continue
        tid = str(cell.get("transcript_id") or "")
        turns_at_cp = int(cell.get("turn_hi") or 0)
        sealed_hi = max_turn_by_tid.get(tid, 0)
        post_lid = post_lid_by_tid.get(tid, 0)
        if turns_at_cp > sealed_hi + post_lid:
            mismatches.append(
                {
                    "transcript_id": tid,
                    "turns_at_cp": turns_at_cp,
                    "sealed_turn_hi": sealed_hi,
                    "post_lid_turns": post_lid,
                }
            )
    return mismatches


def compute_tools_available(
    *,
    segments: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tools: Tools,
    thread_id: str,
) -> bool:
    if tools != "openai":
        return False
    if thread_id == "10223":
        return False
    if any(seg.get("from_sealed", True) for seg in segments):
        return False
    if any(msg.get("source") != "cursor-jsonl" for msg in messages):
        return False
    return bool(messages)


def segment_codec_counts(segments: list[dict[str, Any]]) -> dict[str, int]:
    md_v1 = sum(1 for s in segments if s.get("from_sealed", True))
    messages_v1 = sum(1 for s in segments if s.get("verbatim_codec") == "messages-v1")
    if messages_v1 == 0:
        return {"md-v1": md_v1, "messages-v1": 0}
    return {"md-v1": md_v1 - messages_v1, "messages-v1": messages_v1}


def render_tape(
    *,
    thread_id: str,
    budget_bytes: int = _DEFAULT_BUDGET_BYTES,
    harvest_stats: dict[str, Any] | None = None,
    scope: str = "last_session",
    transcript_id: str | None = None,
    prior_cells: int = 1,
    include_extras: bool = False,
    tools: Tools = "none",
) -> dict[str, Any]:
    """Render messages+extras dump for a continuity lane (read-only)."""
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
    from cortex_store.dispatch_ops._shared import _FILES_ROOT

    lane_journals, excluded, _explicit = filter_lane_journals(
        thread_id=thread_id, journals=journals
    )
    segments = build_lane_segments(
        lane_journals=lane_journals,
        files_root=_FILES_ROOT,
        excluded=excluded,
    )
    (
        messages,
        index_rows,
        cells,
        truncated,
        payload_bytes,
        tools_available,
        degraded,
    ) = pour_lane_messages(
        thread_id=thread_id,
        journals=journals,
        segments=segments,
        lane_journals=lane_journals,
        files_root=_FILES_ROOT,
        scope=scope,
        transcript_id=transcript_id,
        prior_cells=prior_cells,
        budget_bytes=budget_bytes,
        tools=tools,
        include_extras=include_extras,
    )
    if scope == "window" and transcript_id:
        segments = _window_segments(segments, cells)
    from cortex_store.events_tape import (
        agent_bus_tape_rendered,
        agent_bus_tape_segment_unavailable,
    )

    for ex in excluded:
        if ex.get("reason") == "segment_unavailable":
            agent_bus_tape_segment_unavailable(
                thread_id=thread_id,
                session_id=str(ex["session_id"]),
                depth=str(ex.get("depth") or "light"),
            )
    agent_bus_tape_rendered(
        thread_id=thread_id,
        segment_count=len(segments),
        turn_count=sum(s.get("turn_count", 0) for s in segments),
        truncated=truncated,
        scope=scope,
        tools=tools,
        include_extras=include_extras,
        index_count=len(index_rows),
        segment_codec_counts=segment_codec_counts(segments),
        surfaces=["cursor"],
        tools_available=tools_available,
    )
    mismatch = _build_mismatch_rows(cells=cells, segments=segments)
    open_line = build_open_line(
        thread_id=thread_id,
        segments=segments,
        cells=cells,
        messages=messages,
        index_rows=index_rows,
        excluded=excluded,
        truncated=truncated,
        budget_bytes=budget_bytes,
        payload_bytes=payload_bytes,
        harvest=harvest_stats,
        mismatch=mismatch,
        scope=scope,
        transcript_id=transcript_id,
        prior_cells=prior_cells,
        tools_available=tools_available,
        degraded=degraded,
    )
    meta = {
        "messages_sha256": messages_sha256(messages),
        "tools": tools,
        "tools_available": tools_available,
        "extras": include_extras,
        "turn_count": open_line["turn_count"],
        "message_count": len(messages),
        "truncated": truncated,
        "surface": "cursor",
    }
    body: dict[str, Any] = {
        "segments": segments,
        "cells": cells,
        "messages": messages,
        "index": index_rows,
        "excluded": excluded,
        "truncated": truncated,
        "turn_count": sum(s.get("turn_count", 0) for s in segments),
        "segment_count": len(segments),
        "mismatch": mismatch,
        "meta": meta,
    }
    if truncated and degraded is not None:
        body["degraded"] = degraded
    return {"open_line": open_line, "summary": summary_line(open_line), **body}


def summary_line(open_line: dict[str, Any]) -> str:
    harvest = open_line.get("harvest")
    harvest_part = ""
    if isinstance(harvest, dict):
        harvest_part = (
            f" harvest sealed={harvest.get('sealed', 0)}/{harvest.get('discovered', 0)}"
        )
    return (
        f"Tape {open_line['thread_id']}: {open_line['segment_count']} segments, "
        f"{open_line['message_count']} messages, "
        f"{open_line['payload_bytes']}B/{open_line['budget_bytes']}B"
        f"{', truncated' if open_line['truncated'] else ''}{harvest_part}."
    )


__all__ = [
    "render_tape",
    "TapeSegment",
    "build_chain_segments",
    "_binding_for_journal",
    "_build_mismatch_rows",
    "_cells_for_lane",
    "_last_session_cells",
    "_filter_messages_to_cells",
    "anchor_jsonl_messages",
    "_load_live_anchor_transcript",
    "_find_jsonl_for_uuid",
    "segment_codec_counts",
    "summary_line",
    "connect",
    "list_checkpoint_turns",
]
