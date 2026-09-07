"""CP-cell partition and messages+extras tape render on read."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from cortex_store.session_close_successor_hop import lookup_sealed_journal
from cortex_store.verbatim_succession import (
    journal_verbatim_bytes,
    split_verbatim_layer,
    verbatim_fingerprint,
)

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
    digest, _ = verbatim_fingerprint(text)
    return digest


def _split_verbatim_layer(full_md: str, *, verbatim_bytes: int | None = None) -> str:
    return split_verbatim_layer(full_md, verbatim_bytes=verbatim_bytes)


def _turn_count_verbatim(verbatim: str) -> int:
    return sum(1 for line in verbatim.splitlines() if line.startswith("## Turn"))


def _parse_entity_ids(journal: dict[str, Any]) -> list[str]:
    entity_ids = journal.get("entity_ids") or []
    if isinstance(entity_ids, str):
        entity_ids = json.loads(entity_ids)
    return [str(x) for x in entity_ids]


def _journal_cites_lane(journal: dict[str, Any], thread_id: str) -> bool:
    from cortex_store.dispatch_ops._session_bus_thread_disposition import (
        parse_bus_thread_refs,
    )

    return thread_id in parse_bus_thread_refs(_parse_entity_ids(journal))


def _explicit_uuids_for_lane(thread_id: str) -> set[str]:
    """Collect transcript ids named in CP ``Window:`` anchors on *thread_id*."""
    explicit: set[str] = set()
    for cp in list_checkpoint_turns(thread_id=thread_id):
        with connect() as conn:
            row = conn.execute(
                "SELECT body FROM turns WHERE thread = ? AND turn_number = ?",
                (thread_id, cp.turn_number),
            ).fetchone()
        body = str(row["body"]) if row else ""
        for anchor in _parse_window_lines(body):
            tid = anchor.get("transcript_id")
            if tid:
                explicit.add(str(tid))
    return explicit


def _binding_for_journal(
    journal: dict[str, Any],
    thread_id: str,
    *,
    explicit_uuids: set[str] | None = None,
) -> tuple[str | None, str | None]:
    """Derive ``(binding, dominant_lane)`` for render; ``None`` binding ⇒ dropped."""
    explicit = explicit_uuids or set()
    dominant_lane = journal.get("dominant_lane")
    if isinstance(dominant_lane, str):
        dominant_lane = dominant_lane.strip() or None
    uuid = journal.get("conversation_uuid")
    uuid_str = str(uuid) if uuid else None
    cites = _journal_cites_lane(journal, thread_id)
    is_explicit = uuid_str is not None and uuid_str in explicit
    closed_by = journal.get("closed_by")
    is_succession = closed_by == "succession"
    on_tape = cites or is_explicit or dominant_lane == thread_id or is_succession

    if not on_tape:
        if dominant_lane and dominant_lane != thread_id:
            return "read_only", dominant_lane
        return None, dominant_lane

    if is_explicit:
        return "explicit_cp", dominant_lane or thread_id
    if cites or dominant_lane == thread_id:
        return "dominant_write", dominant_lane or thread_id
    if is_succession:
        return "sole", dominant_lane or thread_id
    return None, dominant_lane


def _ordered_chain_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order rows sharing a conversation_uuid by prior_session_id chain (R7a)."""
    by_uuid: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        uuid = row.get("conversation_uuid")
        if not uuid:
            continue
        by_uuid.setdefault(str(uuid), []).append(row)
    ordered: list[dict[str, Any]] = []
    for uuid_rows in by_uuid.values():
        by_sid = {
            str(r["session_id"]): r for r in uuid_rows if r.get("session_id")
        }
        roots = [
            r
            for r in uuid_rows
            if not r.get("prior_session_id")
            or str(r["prior_session_id"]) not in by_sid
        ]
        if not roots:
            ordered.extend(sorted(uuid_rows, key=lambda r: int(r.get("id") or 0)))
            continue
        chain = [roots[0]]
        while True:
            child = next(
                (
                    r
                    for r in uuid_rows
                    if str(r.get("prior_session_id") or "") == str(chain[-1]["session_id"])
                ),
                None,
            )
            if child is None:
                break
            chain.append(child)
        ordered.extend(chain)
    solo = [r for r in rows if not r.get("conversation_uuid")]
    ordered.extend(solo)
    return ordered


def build_chain_segments(
    journals: list[dict[str, Any]], *, files_root: Any
) -> list[dict[str, Any]]:
    """Slice uuid chains into (turn_lo, turn_hi] segments without double render (I8)."""
    segments: list[dict[str, Any]] = []
    prior_turn_by_uuid: dict[str, int] = {}
    for journal in _ordered_chain_rows(journals):
        sid = journal.get("session_id")
        if not sid or not journal.get("file_path"):
            continue
        path = files_root / journal["file_path"]
        if not path.is_file():
            continue
        full = path.read_text(encoding="utf-8")
        verbatim = _split_verbatim_layer(
            full, verbatim_bytes=journal_verbatim_bytes(journal)
        )
        turn_count = _turn_count_verbatim(verbatim)
        uuid = str(journal.get("conversation_uuid") or sid)
        turn_lo = prior_turn_by_uuid.get(uuid, 0)
        turn_hi = turn_count
        prior_turn_by_uuid[uuid] = turn_hi
        if turn_hi <= turn_lo:
            continue
        segments.append(
            {
                "session_id": sid,
                "transcript_id": uuid,
                "turn_lo": turn_lo,
                "turn_hi": turn_hi,
                "turn_count": turn_hi - turn_lo,
                "verbatim_sha256": _verbatim_sha256(verbatim),
                "conversation_uuid": journal.get("conversation_uuid"),
            }
        )
    return segments


def _load_sealed_segment(session_id: str) -> TapeSegment | None:
    row = lookup_sealed_journal(session_id)
    if row is None:
        return None
    from cortex_store.db import cortex_conn

    conn = cortex_conn()
    try:
        journal = conn.execute(
            "SELECT file_path, conversation_uuid, closed_by, verbatim_bytes "
            "FROM session_journals WHERE session_id = ?",
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
    verbatim = _split_verbatim_layer(
        full, verbatim_bytes=journal_verbatim_bytes(journal)
    )
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


_TURN_HEADING_RE = re.compile(r"^## Turn (\d+)")


def _verbatim_messages_from_segment(
    verbatim: str,
    *,
    seg: dict[str, Any],
    session_id: str,
) -> list[dict[str, Any]]:
    """Parse ``### User`` / ``### Assistant`` blocks into speech messages (AC-9/18)."""
    turn_lo = int(seg.get("turn_lo") or 0)
    turn_hi = seg.get("turn_hi")
    turn_hi_int = int(turn_hi) if turn_hi is not None else None
    transcript_id = str(seg["transcript_id"])
    window_whole = seg.get("boundary") == "window_whole"

    messages: list[dict[str, Any]] = []
    current_turn = 0
    current_role: str | None = None
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal body_lines, current_role
        if current_role is None:
            body_lines = []
            return
        if turn_hi_int is not None:
            if current_turn <= turn_lo or current_turn > turn_hi_int:
                body_lines = []
                current_role = None
                return
        content = "\n".join(body_lines).strip()
        if content:
            messages.append(
                {
                    "role": current_role,
                    "content": content,
                    "transcript_id": transcript_id,
                    "session_id": session_id,
                    "turn_index": current_turn,
                    "window_whole": window_whole,
                }
            )
        body_lines = []
        current_role = None

    for line in verbatim.splitlines():
        turn_match = _TURN_HEADING_RE.match(line)
        if turn_match:
            flush()
            current_turn = int(turn_match.group(1))
            continue
        if line.startswith("### User"):
            flush()
            current_role = "user"
            continue
        if line.startswith("### "):
            flush()
            current_role = "assistant"
            continue
        if current_role is not None:
            body_lines.append(line)
    flush()
    return messages


def _find_jsonl_for_uuid(conversation_uuid: str) -> Any | None:
    from cortex_store.transcript_assembly import _transcripts_root
    from cortex_store.session_close_successor_hop import conversation_uuid_from_jsonl_path
    from cortex_store.transcript_session_id import _jsonl_paths_by_mtime_desc

    root = _transcripts_root()
    for jsonl_path in _jsonl_paths_by_mtime_desc(root):
        try:
            if conversation_uuid_from_jsonl_path(jsonl_path) == conversation_uuid:
                return jsonl_path
        except (OSError, ValueError):
            continue
    return None


def _post_lid_tail(
    *,
    conversation_uuid: str | None,
    sealed_turn_count: int,
    session_id: str,
) -> tuple[int, str | None]:
    """Return live JSONL turns beyond *sealed_turn_count* (AC-5)."""
    if not conversation_uuid or sealed_turn_count < 0:
        return 0, None
    jsonl_path = _find_jsonl_for_uuid(str(conversation_uuid))
    if jsonl_path is None:
        return 0, None
    from cortex_store.transcript_assembly import assemble_verbatim_md

    try:
        live_md, live_turns = assemble_verbatim_md(
            jsonl_path=jsonl_path,
            session_id=session_id,
        )
    except (OSError, ValueError):
        return 0, None
    if live_turns <= sealed_turn_count:
        return 0, None
    return live_turns - sealed_turn_count, live_md


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


def _last_turns_at_cp(
    cp_anchors: list[tuple[Any, list[dict[str, Any]]]],
    *,
    transcript_id: str,
    before_index: int | None = None,
) -> int:
    """Return the last ``turns@cp`` anchor for *transcript_id* before *before_index*."""
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
    """Join CP window anchors with R7a chain segments (AC-14 / AC-18)."""
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
                cp_anchors,
                transcript_id=transcript_id,
                before_index=idx,
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
            continue
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


def _bus_turn_id_for_turn(
    cells: list[dict[str, Any]],
    *,
    transcript_id: str,
    turn_index: int,
) -> int | None:
    for cell in cells:
        if str(cell.get("transcript_id") or "") != transcript_id:
            continue
        turn_lo = int(cell.get("turn_lo") or 0)
        turn_hi = int(cell.get("turn_hi") or 0)
        if turn_lo < turn_index <= turn_hi:
            bus_turn_id = cell.get("bus_turn_id")
            return int(bus_turn_id) if bus_turn_id is not None else None
    return None


def _degrade_overflow_messages(
    messages: list[dict[str, Any]],
    *,
    cells: list[dict[str, Any]],
    budget_bytes: int,
) -> tuple[list[dict[str, Any]], bool]:
    """AC-6: overflow drops oldest speech into Index lines with locate pointers."""
    payload_bytes = len(json.dumps(messages).encode("utf-8"))
    if payload_bytes <= budget_bytes:
        return messages, False
    kept = messages[-max(1, budget_bytes // 256) :]
    dropped = messages[: len(messages) - len(kept)]
    index_lines: list[dict[str, Any]] = []
    for msg in dropped:
        sid = str(msg.get("session_id") or "")
        turn_index = int(msg.get("turn_index") or 0)
        tid = str(msg.get("transcript_id") or "")
        index_lines.append(
            {
                "role": "index",
                "content": f"transcript:{sid}#turn-{turn_index}",
                "transcript_span": f"transcript:{sid}#turn-{turn_index}",
                "bus_turn_id": _bus_turn_id_for_turn(
                    cells,
                    transcript_id=tid,
                    turn_index=turn_index,
                ),
                "session_id": sid,
                "transcript_id": tid,
                "turn_index": turn_index,
            }
        )
    return index_lines + kept, True


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
    lane_journals: list[dict[str, Any]] = []
    explicit_uuids = _explicit_uuids_for_lane(thread_id)
    for journal in journals:
        sid = journal.get("session_id")
        if not sid:
            continue
        binding, dominant_lane = _binding_for_journal(
            journal, thread_id, explicit_uuids=explicit_uuids
        )
        if binding is None:
            excluded.append(
                {
                    "session_id": sid,
                    "reason": "dropped",
                    "detail": "human row neither cites lane nor is CP-named",
                }
            )
            continue
        if binding == "read_only":
            excluded.append(
                {
                    "session_id": sid,
                    "reason": "read_only",
                    "detail": f"foreign dominant_lane={dominant_lane}",
                    "dominant_lane": dominant_lane,
                }
            )
            continue
        if journal.get("file_path") is None:
            excluded.append({"session_id": sid, "reason": "segment_unavailable", "depth": "light"})
            continue
        journal = {**journal, "_binding": binding, "_dominant_lane": dominant_lane}
        lane_journals.append(journal)

    from cortex_store.dispatch_ops._shared import _FILES_ROOT

    for chain_seg in build_chain_segments(lane_journals, files_root=_FILES_ROOT):
        journal = next(
            j for j in lane_journals if j.get("session_id") == chain_seg["session_id"]
        )
        binding = journal.get("_binding", "dominant_write")
        dominant_lane = journal.get("_dominant_lane")
        sealed_full_turns = 0
        post_lid_turns = 0
        file_path = journal.get("file_path")
        if file_path:
            full = (_FILES_ROOT / file_path).read_text(encoding="utf-8")
            verbatim = _split_verbatim_layer(
                full, verbatim_bytes=journal_verbatim_bytes(journal)
            )
            sealed_full_turns = _turn_count_verbatim(verbatim)
            post_lid_turns, _ = _post_lid_tail(
                conversation_uuid=journal.get("conversation_uuid"),
                sealed_turn_count=sealed_full_turns,
                session_id=str(chain_seg["session_id"]),
            )
        segments.append(
            {
                "session_id": chain_seg["session_id"],
                "transcript_id": chain_seg["transcript_id"],
                "turn_count": chain_seg["turn_count"],
                "turn_lo": chain_seg["turn_lo"],
                "turn_hi": chain_seg["turn_hi"],
                "verbatim_sha256": chain_seg["verbatim_sha256"],
                "conversation_uuid": chain_seg["conversation_uuid"],
                "binding": binding,
                "dominant_lane": dominant_lane,
                "post_lid_turns": post_lid_turns,
                "boundary": None,
            }
        )

    for journal in lane_journals:
        sid = journal.get("session_id")
        if sid and not any(s["session_id"] == sid for s in segments):
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
                    "binding": journal.get("_binding", seg.binding),
                    "dominant_lane": journal.get("_dominant_lane"),
                    "boundary": seg.boundary or (
                        "window_whole" if seg.turn_count >= 0 else None
                    ),
                }
            )

    cells = _cells_for_lane(
        thread_id=thread_id,
        lane_journals=lane_journals,
        files_root=_FILES_ROOT,
    )
    messages: list[dict[str, Any]] = []
    for seg in segments:
        sid = seg["session_id"]
        from cortex_store.dispatch_ops._shared import _FILES_ROOT

        journal = next(j for j in journals if j.get("session_id") == sid)
        file_path = journal.get("file_path")
        if not file_path:
            continue
        full = (_FILES_ROOT / file_path).read_text(encoding="utf-8")
        verbatim = _split_verbatim_layer(
            full, verbatim_bytes=journal_verbatim_bytes(journal)
        )
        messages.extend(
            _verbatim_messages_from_segment(
                verbatim,
                seg=seg,
                session_id=sid,
            )
        )
        post_lid = int(seg.get("post_lid_turns") or 0)
        if post_lid > 0:
            sealed_full = sum(
                1 for line in verbatim.splitlines() if line.startswith("## Turn")
            )
            _, live_md = _post_lid_tail(
                conversation_uuid=journal.get("conversation_uuid"),
                sealed_turn_count=sealed_full,
                session_id=sid,
            )
            if live_md:
                messages.extend(
                    _verbatim_messages_from_segment(
                        live_md,
                        seg={
                            **seg,
                            "turn_lo": sealed_full,
                            "turn_hi": sealed_full + post_lid,
                        },
                        session_id=sid,
                    )
                )

    payload_bytes = len(json.dumps(messages).encode("utf-8"))
    truncated = payload_bytes > budget_bytes
    if truncated:
        messages, truncated = _degrade_overflow_messages(
            messages,
            cells=cells,
            budget_bytes=budget_bytes,
        )

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


__all__ = ["render_tape", "TapeSegment", "build_chain_segments", "_binding_for_journal"]
