"""Tape message pour and open_line projection (Phase 2 split)."""

from __future__ import annotations

from typing import Any

from continuity_tape.messages import Tools, apply_tools_policy, strip_extras
from continuity_tape.seal_reader import messages_from_sealed_row
from cortex_store.verbatim_succession import (
    journal_verbatim_bytes,
    split_verbatim_layer,
)

from .tape_cells import (
    _cells_for_lane,
    _filter_messages_to_cells,
    _last_session_cells,
    _window_cells,
)
from .tape_membership import _turn_count_verbatim


def pour_lane_messages(
    *,
    thread_id: str,
    journals: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    lane_journals: list[dict[str, Any]],
    files_root: Any,
    scope: str,
    transcript_id: str | None = None,
    prior_cells: int = 1,
    budget_bytes: int,
    tools: Tools,
    include_extras: bool,
    channel: str = "continuity",
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
    int,
    bool,
    dict[str, Any] | None,
    int,
]:
    from agent_bus_store import tape_render as tape_live
    from agent_bus_store.tape_degrade import degrade_overflow_messages, payload_bytes

    cells = _cells_for_lane(
        thread_id=thread_id, lane_journals=lane_journals, files_root=files_root
    )
    messages: list[dict[str, Any]] = []
    for seg in segments:
        sid = seg["session_id"]
        journal = next(j for j in journals if j.get("session_id") == sid)
        file_path = journal.get("file_path")
        verbatim = None
        if file_path:
            full = (files_root / file_path).read_text(encoding="utf-8")
            verbatim = split_verbatim_layer(
                full, verbatim_bytes=journal_verbatim_bytes(journal)
            )
        messages.extend(
            messages_from_sealed_row(
                journal,
                seg=seg,
                session_id=sid,
                files_root=files_root,
                verbatim=verbatim,
            )
        )
        post_lid = int(seg.get("post_lid_turns") or 0)
        if post_lid > 0 and verbatim is not None:
            sealed_full = _turn_count_verbatim(verbatim)
            _, tail_messages = tape_live.post_lid_tail(
                conversation_uuid=journal.get("conversation_uuid"),
                sealed_turn_count=sealed_full,
                session_id=sid,
            )
            if tail_messages:
                turn_lo, turn_hi = sealed_full, sealed_full + post_lid
                for msg in tail_messages:
                    turn_index = int(msg.get("turn_index") or 0)
                    if turn_lo < turn_index <= turn_hi:
                        messages.append({**msg, "window_whole": False})
    messages.extend(tape_live.anchor_jsonl_messages(thread_id, existing=messages))
    hop_cells_skipped = 0
    if scope == "window" and transcript_id:
        cells = _window_cells(
            cells, transcript_id=transcript_id, prior_cells=prior_cells
        )
        messages = _filter_messages_to_cells(messages, cells)
    elif scope == "last_session":
        cells, hop_cells_skipped = _last_session_cells(cells, channel=channel)
        messages = _filter_messages_to_cells(messages, cells)
    truncated = payload_bytes(messages, []) > budget_bytes
    index_rows: list[dict[str, Any]] = []
    degraded: dict[str, Any] | None = None
    if truncated:
        messages, index_rows, truncated, degraded = degrade_overflow_messages(
            messages,
            cells=cells,
            budget_bytes=budget_bytes,
            thread_id=thread_id,
        )
    tools_available = tape_live.compute_tools_available(
        segments=segments, messages=messages, tools=tools, thread_id=thread_id
    )
    messages = apply_tools_policy(messages, tools=tools)
    if not include_extras:
        messages = strip_extras(messages)
    payload_bytes_val = payload_bytes(messages, index_rows)
    return (
        messages,
        index_rows,
        cells,
        truncated,
        payload_bytes_val,
        tools_available,
        degraded,
        hop_cells_skipped,
    )


def build_open_line(
    *,
    thread_id: str,
    segments: list[dict[str, Any]],
    cells: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    index_rows: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    truncated: bool,
    budget_bytes: int,
    payload_bytes: int,
    harvest: dict[str, Any] | None,
    mismatch: list[dict[str, Any]],
    scope: str,
    transcript_id: str | None = None,
    prior_cells: int = 1,
    tools_available: bool,
    degraded: dict[str, Any] | None = None,
    hop_cells_skipped: int = 0,
) -> dict[str, Any]:
    from agent_bus_store import tape_render as tape_meta
    last_cp: dict[str, Any] | None = None
    for cell in reversed(cells):
        if cell.get("bus_turn_id") is not None:
            last_cp = {
                "cp_ordinal": cell.get("cp_ordinal"),
                "bus_turn_id": cell.get("bus_turn_id"),
                "transcript_id": cell.get("transcript_id"),
                "turn_lo": cell.get("turn_lo"),
                "turn_hi": cell.get("turn_hi"),
                "channel": cell.get("channel") or "continuity",
            }
            break
    open_cells = [c for c in cells if c.get("bus_turn_id") is None]
    open_interval = {
        "transcript_ids": sorted(
            {str(c.get("transcript_id") or "") for c in open_cells if c.get("transcript_id")}
        ),
        "turns": sum(
            max(0, int(c.get("turn_hi") or 0) - int(c.get("turn_lo") or 0))
            for c in open_cells
        ),
    }
    counts = {
        "read_only": sum(1 for e in excluded if e.get("reason") == "read_only"),
        "foreign_dominant": sum(1 for e in excluded if e.get("reason") == "foreign_dominant"),
        "no_touch": sum(1 for e in excluded if e.get("reason") == "no_touch"),
        "dropped": sum(1 for e in excluded if e.get("reason") == "dropped"),
        "segment_unavailable": sum(
            1 for e in excluded if e.get("reason") == "segment_unavailable"
        ),
    }
    open_line: dict[str, Any] = {
        "thread_id": thread_id,
        "scope": scope,
        "segment_count": len(segments),
        "turn_count": sum(s.get("turn_count", 0) for s in segments),
        "message_count": len(messages),
        "index_count": len(index_rows),
        "truncated": truncated,
        "budget_bytes": budget_bytes,
        "payload_bytes": payload_bytes,
        "segment_codec_counts": tape_meta.segment_codec_counts(segments),
        "surfaces": ["cursor"],
        "last_cp": last_cp,
        "open_interval": open_interval,
        "excluded_counts": counts,
        "harvest": harvest,
        "mismatch": mismatch,
        "tools_available": tools_available,
        "hop_cells_skipped": hop_cells_skipped,
    }
    if truncated and degraded is not None:
        open_line["degraded"] = degraded
    if scope == "window" and transcript_id:
        open_line["window"] = {
            "transcript_id": transcript_id,
            "prior_cells": prior_cells,
            "cell_count": len(cells),
            "cp_ordinals": [int(c.get("cp_ordinal") or 0) for c in cells],
        }
    return open_line


__all__ = [
    "build_open_line",
    "pour_lane_messages",
]
