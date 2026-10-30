"""CP-cell partition for continuity tape reads."""

from __future__ import annotations

from typing import Any

from .checkpoint_windows_render import list_checkpoint_turns
from .db.connection import connect
from .tape_membership import (
    _parse_window_lines,
    build_chain_segments,
    resolve_cell_ownership,
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


def _transcript_ids_in_anchors(anchors: list[dict[str, Any]]) -> set[str]:
    return {
        str(anchor["transcript_id"])
        for anchor in anchors
        if anchor.get("transcript_id")
    }


def _wall_ordinal_from_cells(cells: list[dict[str, Any]]) -> int | None:
    wall: int | None = None
    for cell in cells:
        if cell.get("bus_turn_id") is None:
            continue
        wall = int(cell.get("cp_ordinal") or 0)
    return wall


def _last_sealed_cp_ordinal_for_transcript(
    cells: list[dict[str, Any]],
    *,
    transcript_id: str,
    ignore_window_whole_at_cp: int | None = None,
) -> int | None:
    best: int | None = None
    for cell in cells:
        if cell.get("bus_turn_id") is None:
            continue
        if str(cell.get("transcript_id") or "") != transcript_id:
            continue
        ordinal = int(cell.get("cp_ordinal") or 0)
        if (
            ignore_window_whole_at_cp is not None
            and ordinal == ignore_window_whole_at_cp
            and cell.get("boundary") == "window_whole"
        ):
            continue
        if best is None or ordinal > best:
            best = ordinal
    return best


def _open_cell_is_superseded(
    cell: dict[str, Any],
    *,
    cells: list[dict[str, Any]],
    wall_ordinal: int | None,
) -> bool:
    """True when a post-wall open cell belongs to a transcript sealed before the wall."""
    if wall_ordinal is None:
        return False
    tid = str(cell.get("transcript_id") or "")
    last_sealed = _last_sealed_cp_ordinal_for_transcript(cells, transcript_id=tid)
    if last_sealed is None:
        return False
    return last_sealed < wall_ordinal


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
                    "channel": "continuity",
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
                cell_channel = anchor.get("channel") or "continuity"
                for seg in targets:
                    cells.append(
                        {
                            "cp_ordinal": cp.cp_ordinal,
                            "transcript_id": seg["transcript_id"],
                            "turn_lo": seg["turn_lo"],
                            "turn_hi": seg["turn_hi"],
                            "boundary": "window_whole",
                            "bus_turn_id": cp.turn_number,
                            "channel": cell_channel,
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
                    "channel": anchor.get("channel") or "continuity",
                }
            )
    last_cp, tip_anchors = cp_anchors[-1]
    tip_transcript_ids = _transcript_ids_in_anchors(tip_anchors)
    for transcript_id in sorted(seen_transcript_ids):
        last_sealed = _last_sealed_cp_ordinal_for_transcript(
            cells,
            transcript_id=transcript_id,
            ignore_window_whole_at_cp=last_cp.cp_ordinal,
        )
        if last_sealed is not None and last_sealed < last_cp.cp_ordinal:
            continue
        if tip_transcript_ids:
            if transcript_id not in tip_transcript_ids and last_sealed is not None:
                continue
        turn_lo = _last_turns_at_cp(cp_anchors, transcript_id=transcript_id)
        turn_hi = max_turn_by_tid.get(transcript_id, turn_lo)
        if turn_hi <= turn_lo:
            continue
        cells.append(
            {
                "cp_ordinal": last_cp.cp_ordinal + 1,
                "transcript_id": transcript_id,
                "turn_lo": turn_lo,
                "turn_hi": turn_hi,
                "boundary": None,
                "bus_turn_id": None,
                "channel": "continuity",
            }
        )
    for cell in cells:
        tid = str(cell.get("transcript_id") or "")
        if not tid:
            continue
        dominant_lane, ownership_source = resolve_cell_ownership(
            tid, lane_journals=lane_journals
        )
        cell["dominant_lane"] = dominant_lane
        cell["ownership_source"] = ownership_source
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
    cell_tids = {str(c.get("transcript_id") or "") for c in cells}
    return [
        s
        for s in segments
        if str(s.get("transcript_id") or "") in cell_tids
    ]


def _last_session_cells(
    cells: list[dict[str, Any]],
    *,
    thread_id: str,
    channel: str = "continuity",
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Select last_session cells at the latest checkpoint wall; drop foreign cells."""
    del channel  # hop and continuity checkpoints both anchor the resume wall
    wall_ordinal: int | None = None
    for cell in reversed(cells):
        if cell.get("bus_turn_id") is None:
            continue
        wall_ordinal = int(cell.get("cp_ordinal") or 0)
        break
    if wall_ordinal is None:
        filtered = list(cells)
        skipped = 0
    else:
        filtered = [
            cell
            for cell in cells
            if cell.get("bus_turn_id") is None
            or int(cell.get("cp_ordinal") or 0) == wall_ordinal
        ]
        skipped = 0
    ownership_unresolved = sum(
        1 for c in filtered if c.get("ownership_source") == "unresolved"
    )
    scoped: list[dict[str, Any]] = []
    foreign_cells_excluded = 0
    superseded_open_excluded = 0
    for cell in filtered:
        if (
            cell.get("bus_turn_id") is None
            and _open_cell_is_superseded(
                cell, cells=cells, wall_ordinal=wall_ordinal
            )
        ):
            superseded_open_excluded += 1
            continue
        dl = cell.get("dominant_lane")
        if dl is not None and str(dl) != thread_id:
            foreign_cells_excluded += 1
            continue
        scoped.append(cell)
    skipped += superseded_open_excluded
    return scoped, skipped, foreign_cells_excluded, ownership_unresolved


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


def _narrow_last_session_pour_cells(
    cells: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pour post-checkpoint open tail only, or a minimal sealed tail when fully sealed."""
    wall_ordinal = _wall_ordinal_from_cells(cells)
    open_cells = [
        c
        for c in cells
        if c.get("bus_turn_id") is None
        and not _open_cell_is_superseded(
            c, cells=cells, wall_ordinal=wall_ordinal
        )
    ]
    open_turns = sum(
        max(0, int(c.get("turn_hi") or 0) - int(c.get("turn_lo") or 0))
        for c in open_cells
    )
    sealed = [c for c in cells if c.get("bus_turn_id") is not None]
    if wall_ordinal is not None:
        sealed = [
            c
            for c in sealed
            if int(c.get("cp_ordinal") or 0) == wall_ordinal
        ]
    narrowed_sealed: list[dict[str, Any]] = []
    for cell in sealed:
        copy = dict(cell)
        turn_lo = int(copy.get("turn_lo") or 0)
        turn_hi = int(copy.get("turn_hi") or 0)
        if turn_hi - turn_lo > 2:
            copy["turn_lo"] = turn_hi - 2
        narrowed_sealed.append(copy)
    if open_turns > 0:
        return narrowed_sealed + open_cells
    if not narrowed_sealed:
        return cells
    return narrowed_sealed


__all__ = [
    "_cells_for_lane",
    "_filter_messages_to_cells",
    "_last_session_cells",
    "_window_cells",
    "_window_segments",
    "_narrow_last_session_pour_cells",
]
