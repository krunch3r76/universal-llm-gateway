"""CP-cell partition and messages+extras tape render on read."""

from __future__ import annotations

from typing import Any

from continuity_tape.messages import Tools, messages_sha256

from .checkpoint_windows_render import list_checkpoint_turns
from .db.connection import connect
from .tape_cells import (
    _cells_for_lane,
    _filter_messages_to_cells,
    _last_session_cells,
    _window_segments,
)
from .tape_degrade import TAPE_BUDGET_BYTES_DEFAULT
from .tape_membership import (
    TapeSegment,
    _binding_for_journal,
    build_chain_segments,
    build_lane_segments,
    filter_lane_journals,
)
from .tape_pour import build_open_line, pour_lane_messages


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
    counts: dict[str, int] = {}
    for seg in segments:
        if seg.get("codec_used") is None and seg.get("verbatim_codec") is None:
            codec = "none"
        else:
            codec = str(seg.get("codec_used") or seg.get("verbatim_codec") or "md-v1")
        counts[codec] = counts.get(codec, 0) + 1
    return counts


def render_tape(
    *,
    thread_id: str,
    budget_bytes: int = TAPE_BUDGET_BYTES_DEFAULT,
    harvest_stats: dict[str, Any] | None = None,
    scope: str = "last_session",
    transcript_id: str | None = None,
    prior_cells: int = 1,
    include_extras: bool = False,
    tools: Tools = "none",
    channel: str = "continuity",
    budget_source: str | None = None,
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
        hop_cells_skipped,
        foreign_cells_excluded,
        ownership_unresolved,
        codec_fallback_count,
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
        channel=channel,
        budget_source=budget_source,
    )
    if scope == "window" and transcript_id:
        segments = _window_segments(segments, cells)
    from cortex_store.events_tape import (
        agent_bus_tape_hop_cells_skipped,
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
        budget_source=budget_source,
        foreign_cells_excluded=foreign_cells_excluded,
        ownership_unresolved=ownership_unresolved,
        codec_fallback_count=codec_fallback_count,
    )
    if hop_cells_skipped > 0:
        wall_cp_ordinal: int | None = None
        wall_bus_turn_id: int | None = None
        for cell in reversed(cells):
            if cell.get("bus_turn_id") is not None:
                wall_cp_ordinal = int(cell.get("cp_ordinal") or 0)
                wall_bus_turn_id = int(cell.get("bus_turn_id") or 0)
                break
        agent_bus_tape_hop_cells_skipped(
            thread_id=thread_id,
            scope=scope,
            channel=channel,
            hop_cells_skipped=hop_cells_skipped,
            wall_cp_ordinal=wall_cp_ordinal,
            wall_bus_turn_id=wall_bus_turn_id,
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
        hop_cells_skipped=hop_cells_skipped,
        foreign_cells_excluded=foreign_cells_excluded,
        ownership_unresolved=ownership_unresolved,
        codec_fallback_count=codec_fallback_count,
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
    "segment_codec_counts",
    "summary_line",
    "connect",
    "list_checkpoint_turns",
]
