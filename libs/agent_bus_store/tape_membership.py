"""CP-cell membership, binding, and chain segments (Phase 2 split)."""

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
    digest, _ = verbatim_fingerprint("md-v1", text)
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


def _binding_for_journal(
    journal: dict[str, Any],
    thread_id: str,
    *,
    explicit_uuids: set[str] | None = None,
) -> tuple[str | None, str | None]:
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


def filter_lane_journals(
    *,
    thread_id: str,
    journals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
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
        lane_journals.append(
            {**journal, "_binding": binding, "_dominant_lane": dominant_lane}
        )
    return lane_journals, excluded, explicit_uuids


def build_lane_segments(
    *,
    lane_journals: list[dict[str, Any]],
    files_root: Any,
    excluded: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for chain_seg in build_chain_segments(lane_journals, files_root=files_root):
        journal = next(
            j for j in lane_journals if j.get("session_id") == chain_seg["session_id"]
        )
        post_lid_turns = 0
        file_path = journal.get("file_path")
        if file_path:
            from agent_bus_store import tape_render as tape_live

            full = (files_root / file_path).read_text(encoding="utf-8")
            verbatim = _split_verbatim_layer(
                full, verbatim_bytes=journal_verbatim_bytes(journal)
            )
            sealed_full_turns = _turn_count_verbatim(verbatim)
            post_lid_turns, _ = tape_live.post_lid_tail(
                conversation_uuid=journal.get("conversation_uuid"),
                sealed_turn_count=sealed_full_turns,
                session_id=str(chain_seg["session_id"]),
            )
        segments.append(
            {
                **chain_seg,
                "binding": journal.get("_binding", "dominant_write"),
                "dominant_lane": journal.get("_dominant_lane"),
                "post_lid_turns": post_lid_turns,
                "boundary": None,
                "from_sealed": True,
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
                    "from_sealed": True,
                }
            )
    return segments


__all__ = [
    "TapeSegment",
    "build_chain_segments",
    "build_lane_segments",
    "_binding_for_journal",
    "_explicit_uuids_for_lane",
    "_load_sealed_segment",
    "_parse_window_lines",
    "_split_verbatim_layer",
    "_turn_count_verbatim",
    "filter_lane_journals",
]
