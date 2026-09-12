"""Tape cells, message pour, and degrade (Phase 2 split)."""

from __future__ import annotations

import json
from typing import Any

from continuity_tape.messages import Tools, apply_tools_policy, strip_extras
from continuity_tape.seal_reader import messages_from_sealed_row
from cortex_store.verbatim_succession import journal_verbatim_bytes, split_verbatim_layer

from .checkpoint_windows_render import list_checkpoint_turns
from .db.connection import connect
from .tape_membership import (
    _parse_window_lines,
    _turn_count_verbatim,
    build_chain_segments,
)


def _last_turns_at_cp(
    cp_anchors: list[tuple[Any, list[dict[str, Any]]]],
    *,
    transcript_id: str,
    before_index: int | None = None,
) -> int:
    limit = len(cp_anchors) if before_index is None else before_index
    last = 0
    for idx in range(limit):
        for anchor in cp_anchors[idx][1]:
            if anchor.get("transcript_id") == transcript_id and anchor.get("turns_at_cp") is not None:
                last = int(anchor["turns_at_cp"])
    return last


def _max_turn_hi_by_transcript(chain_segments: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for seg in chain_segments:
        tid = str(seg["transcript_id"])
        out[tid] = max(out.get(tid, 0), int(seg["turn_hi"]))
    return out


def _cells_for_lane(
    *,
    thread_id: str,
    lane_journals: list[dict[str, Any]],
    files_root: Any,
) -> list[dict[str, Any]]:
    cps = list_checkpoint_turns(thread_id=thread_id)
    chain_segments = build_chain_segments(lane_journals, files_root=files_root)
    max_turn_by_tid = _max_turn_hi_by_transcript(chain_segments)
    cp_anchors: list[tuple[Any, list[dict[str, Any]]]] = []
    for cp in cps:
        with connect() as conn:
            row = conn.execute(
                "SELECT body FROM turns WHERE thread = ? AND turn_number = ?",
                (thread_id, cp.turn_number),
            ).fetchone()
        body = str(row["body"]) if row else ""
        cp_anchors.append((cp, _parse_window_lines(body)))
    cells: list[dict[str, Any]] = []
    if not cp_anchors:
        for seg in chain_segments:
            cells.append(
                {
                    "cp_ordinal": 0,
                    "transcript_id": seg["transcript_id"],
                    "turn_lo": seg["turn_lo"],
                    "turn_hi": seg["turn_hi"],
                    "boundary": "window_whole",
                    "bus_turn_id": None,
                }
            )
        return cells
    seen_transcript_ids: set[str] = set(max_turn_by_tid)
    for _, anchors in cp_anchors:
        for anchor in anchors:
            tid = anchor.get("transcript_id")
            if tid:
                seen_transcript_ids.add(str(tid))
    for idx, (cp, anchors) in enumerate(cp_anchors):
        for anchor in anchors:
            if anchor.get("boundary") == "window_whole":
                tid = anchor.get("transcript_id")
                targets = (
                    [seg for seg in chain_segments if str(seg["transcript_id"]) == str(tid)]
                    if tid
                    else chain_segments
                )
                for seg in targets:
                    cells.append(
                        {
                            "cp_ordinal": cp.cp_ordinal,
                            "transcript_id": seg["transcript_id"],
                            "turn_lo": seg["turn_lo"],
                            "turn_hi": seg["turn_hi"],
                            "boundary": "window_whole",
                            "bus_turn_id": cp.turn_number,
                        }
                    )
                continue
            tid = anchor.get("transcript_id")
            turns_at_cp = anchor.get("turns_at_cp")
            if not tid or turns_at_cp is None:
                continue
            transcript_id = str(tid)
            turn_hi = int(turns_at_cp)
            turn_lo = _last_turns_at_cp(
                cp_anchors, transcript_id=transcript_id, before_index=idx
            )
            if turn_hi <= turn_lo:
                continue
            cells.append(
                {
                    "cp_ordinal": cp.cp_ordinal,
                    "transcript_id": transcript_id,
                    "turn_lo": turn_lo,
                    "turn_hi": turn_hi,
                    "boundary": None,
                    "bus_turn_id": cp.turn_number,
                }
            )
    last_cp = cp_anchors[-1][0]
    for transcript_id in sorted(seen_transcript_ids):
        turn_lo = _last_turns_at_cp(cp_anchors, transcript_id=transcript_id)
        turn_hi = max_turn_by_tid.get(transcript_id, turn_lo)
        if turn_hi <= turn_lo:
            from agent_bus_store import tape_render as tape_live

            live_hi = tape_live.live_jsonl_turn_count(transcript_id)
            if live_hi <= turn_lo:
                continue
            turn_hi = live_hi
        cells.append(
            {
                "cp_ordinal": last_cp.cp_ordinal + 1,
                "transcript_id": transcript_id,
                "turn_lo": turn_lo,
                "turn_hi": turn_hi,
                "boundary": None,
                "bus_turn_id": None,
            }
        )
    return cells


def _window_cells(
    cells: list[dict[str, Any]],
    *,
    transcript_id: str,
    prior_cells: int,
) -> list[dict[str, Any]]:
    window = [
        c for c in cells if str(c.get("transcript_id") or "") == transcript_id
    ]
    if not window:
        return []
    first_idx = next(
        i
        for i, c in enumerate(cells)
        if str(c.get("transcript_id") or "") == transcript_id
    )
    before = cells[:first_idx]
    prior = before[-prior_cells:] if prior_cells > 0 else []
    selected_keys = {
        (
            int(c.get("cp_ordinal") or 0),
            str(c.get("transcript_id") or ""),
            int(c.get("turn_lo") or 0),
            int(c.get("turn_hi") or 0),
            c.get("bus_turn_id"),
        )
        for c in prior + window
    }
    return [
        c
        for c in cells
        if (
            int(c.get("cp_ordinal") or 0),
            str(c.get("transcript_id") or ""),
            int(c.get("turn_lo") or 0),
            int(c.get("turn_hi") or 0),
            c.get("bus_turn_id"),
        )
        in selected_keys
    ]


def _window_segments(
    segments: list[dict[str, Any]],
    cells: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Window read must not list lane segments absent from post-filter cells.
    cell_tids = {str(c.get("transcript_id") or "") for c in cells}
    return [
        s
        for s in segments
        if str(s.get("transcript_id") or "") in cell_tids
    ]


def _last_session_cells(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last_cp_ordinal: int | None = None
    for cell in reversed(cells):
        if cell.get("bus_turn_id") is not None:
            last_cp_ordinal = int(cell.get("cp_ordinal") or 0)
            break
    if last_cp_ordinal is None:
        return list(cells)
    allowed = {last_cp_ordinal, last_cp_ordinal + 1}
    return [cell for cell in cells if int(cell.get("cp_ordinal") or 0) in allowed]


def _message_in_cell(msg: dict[str, Any], cell: dict[str, Any]) -> bool:
    if str(msg.get("transcript_id") or "") != str(cell.get("transcript_id") or ""):
        return False
    turn_index = int(msg.get("turn_index") or 0)
    turn_lo = int(cell.get("turn_lo") or 0)
    turn_hi = int(cell.get("turn_hi") or 0)
    return turn_lo < turn_index <= turn_hi


def _filter_messages_to_cells(
    messages: list[dict[str, Any]],
    cells: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not cells:
        return []
    return [msg for msg in messages if any(_message_in_cell(msg, cell) for cell in cells)]


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
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
    int,
    bool,
    dict[str, Any] | None,
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
    if scope == "window" and transcript_id:
        cells = _window_cells(
            cells, transcript_id=transcript_id, prior_cells=prior_cells
        )
        messages = _filter_messages_to_cells(messages, cells)
    elif scope == "last_session":
        cells = _last_session_cells(cells)
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
    "_cells_for_lane",
    "_filter_messages_to_cells",
    "_last_session_cells",
    "_window_cells",
    "build_open_line",
    "pour_lane_messages",
]
