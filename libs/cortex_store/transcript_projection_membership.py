"""Transcript projection membership — candidate scan, binding, bus joins.

Selects JSONL windows for a root continuity lane, classifies membership via
``binding_for``, joins CHECKPOINT/CLOSEOUT sends to agent-bus turns over HTTP,
and corroborates CP ``Window:`` anchors for mismatch detection.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

from cortex_store.transcript_lane_touch import (
    lane_touches_from_records,
    projection_binding_for,
    projection_dominant_lane,
)
from cortex_store.transcript_projection_facts import (
    BusSendFact,
    WindowFacts,
    parse_records,
    turn_index_at,
)
from cortex_store.transcript_session_id import _jsonl_paths_by_mtime_desc

_WINDOW_ANCHOR_RE = re.compile(
    r"Window:\s*transcript_id=([0-9a-f-]{36})\s*·\s*turns@cp=(\d+)",
    re.I,
)
_HIGHLIGHT_RE = re.compile(r"(?m)^Highlight:\s*(.+)$")
HIGHLIGHT_MAX_CHARS = 600


@dataclass
class AnchorMismatch:
    """CP body anchor that does not match observed JSONL send."""

    cp_turn: int
    cp_ordinal: int | None
    claimed: dict[str, Any]
    observed: dict[str, Any]
    kind: str


@dataclass
class MembershipResult:
    """Outcome of membership classification for one candidate window."""

    transcript_id: str
    member: bool
    lane: str
    binding: str
    exclude_reason: str | None = None
    facts: WindowFacts | None = None
    jsonl_path: Path | None = None


@dataclass
class BusTurnIndex:
    """Agent-bus turns indexed by (thread, subject) for boundary joins."""

    by_thread_subject: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    checkpoint_ordinals: dict[int, int] = field(default_factory=dict)


def _turns_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize agent-bus list-turns responses to a turn row list."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        turns = payload.get("turns")
        if isinstance(turns, list):
            return [row for row in turns if isinstance(row, dict)]
    return []


def _default_bus_get(path: str) -> dict[str, Any] | list[Any] | None:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = client.get(path, headers=headers)
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception:
        return None


def fetch_thread_detail(
    thread_id: str,
    *,
    bus_get: Callable[[str], Any] | None = None,
) -> dict[str, Any] | None:
    """Load thread metadata via agent-bus HTTP."""
    getter = bus_get or _default_bus_get
    data = getter(f"/threads/{thread_id}")
    return data if isinstance(data, dict) else None


def fetch_lineage_children(
    thread_id: str,
    *,
    bus_get: Callable[[str], Any] | None = None,
) -> list[str]:
    """Return substantiated child thread ids from ``GET /threads/{id}/lineage``."""
    getter = bus_get or _default_bus_get
    data = getter(f"/threads/{thread_id}/lineage")
    if not isinstance(data, dict):
        return []
    children = data.get("children") or []
    out: list[str] = []
    for child in children:
        if isinstance(child, dict):
            tid = child.get("thread_id") or child.get("id")
            if tid is not None:
                out.append(str(tid))
    return out


def build_bus_turn_index(
    thread_ids: list[str],
    *,
    bus_get: Callable[[str], Any] | None = None,
) -> BusTurnIndex:
    """Index bus turns by (thread, subject) and rank CHECKPOINT ordinals."""
    getter = bus_get or _default_bus_get
    index = BusTurnIndex()
    for tid in thread_ids:
        payload = getter(f"/turns?thread={tid}")
        turns = _turns_from_payload(payload)
        cp_rank = 0
        for row in turns:
            if not isinstance(row, dict):
                continue
            subject = str(row.get("subject") or "")
            key = (tid, subject)
            if key not in index.by_thread_subject:
                index.by_thread_subject[key] = row
            if subject.upper().startswith("CHECKPOINT"):
                cp_rank += 1
                turn_no = row.get("turn_number")
                if turn_no is not None:
                    index.checkpoint_ordinals[int(turn_no)] = cp_rank
    return index


def jsonl_candidates(
    *,
    transcripts_root: Path,
    thread_created_at: datetime | None,
    sticky_ids: set[str],
    force_ids: set[str],
    scan_limit: int = 200,
) -> list[Path]:
    """Return JSONL paths to consider: recent-by-mtime cap ∪ sticky ∪ forced."""
    ordered = _jsonl_paths_by_mtime_desc(transcripts_root)
    by_mtime = ordered[:scan_limit]
    if thread_created_at is not None:
        cutoff = thread_created_at.timestamp()
        filtered = [p for p in ordered if p.stat().st_mtime >= cutoff]
    else:
        filtered = []
    seen: set[str] = set()
    merged: list[Path] = []
    for path in filtered + by_mtime:
        tid = path.parent.name
        if tid in seen:
            continue
        seen.add(tid)
        merged.append(path)
    by_id = {p.parent.name: p for p in merged}
    for tid in sticky_ids | force_ids:
        candidate = transcripts_root / tid / f"{tid}.jsonl"
        if candidate.is_file():
            by_id[tid] = candidate
    return list(by_id.values())


def classify_membership(
    facts: WindowFacts,
    *,
    root: str,
    children: list[str],
    sticky: bool,
) -> MembershipResult:
    """Decide whether a parsed window belongs to the projection."""
    touches = facts.bus_touch or lane_touches_from_records([])
    lane = projection_dominant_lane(touches, root, children)
    binding = projection_binding_for(lane, touches, transcript_id=facts.transcript_id)
    member_bindings = {"dominant_write", "explicit_cp"}
    child_dominant = any(
        projection_binding_for(child, touches, transcript_id=facts.transcript_id)
        == "dominant_write"
        for child in children
    )
    if binding in member_bindings or child_dominant or sticky:
        return MembershipResult(
            transcript_id=facts.transcript_id,
            member=True,
            lane=lane if binding in member_bindings or sticky else next(
                (
                    c
                    for c in children
                    if projection_binding_for(c, touches) == "dominant_write"
                ),
                lane,
            ),
            binding=binding if binding in member_bindings else "dominant_write",
            facts=facts,
        )
    reason = binding if binding in {"read_only", "foreign_dominant", "no_touch"} else "dropped"
    return MembershipResult(
        transcript_id=facts.transcript_id,
        member=False,
        lane=lane,
        binding=binding,
        exclude_reason=reason,
        facts=facts,
    )


def boundary_sends_for_lane(
    facts: WindowFacts,
    root: str,
    children: list[str],
) -> list[BusSendFact]:
    """Return ascending cell-closing sends for *root* and its children."""
    allowed = {root, *children}
    out = [
        s
        for s in facts.bus_sends
        if s.kind in {"CHECKPOINT", "CLOSEOUT"}
        and (
            (s.kind == "CHECKPOINT" and s.thread == root)
            or (s.kind == "CLOSEOUT" and s.thread in allowed)
        )
    ]
    return sorted(out, key=lambda s: s.turn_index)


def join_boundary_turn_number(
    send: BusSendFact,
    index: BusTurnIndex,
) -> tuple[int | None, int | None]:
    """Look up bus turn_number and cp_ordinal for a JSONL send."""
    row = index.by_thread_subject.get((send.thread, send.subject))
    if row is None:
        return None, None
    turn_number = row.get("turn_number")
    cp_ordinal = None
    if send.kind == "CHECKPOINT" and turn_number is not None:
        cp_ordinal = index.checkpoint_ordinals.get(int(turn_number))
    return (
        int(turn_number) if turn_number is not None else None,
        cp_ordinal,
    )


def extract_cp_highlight(body: str) -> str | None:
    """Parse optional ``Highlight:`` residue line from a CHECKPOINT body."""
    match = _HIGHLIGHT_RE.search(body or "")
    if not match:
        return None
    text = match.group(1).strip()
    if not text:
        return None
    return text[:HIGHLIGHT_MAX_CHARS]


def extract_cp_note(body: str, subject: str) -> tuple[str, str]:
    """Derive cell note from CHECKPOINT body (Layer S v1)."""
    for pattern in (
        r"(?m)^In one line:\s*(.+)$",
        r"(?m)^\*\*Mission:\*\*\s*(.+)$",
    ):
        match = re.search(pattern, body)
        if match:
            return match.group(1).strip()[:240], "cp_one_line"
    if " — " in subject:
        suffix = subject.split(" — ", 1)[1].strip()
        if suffix:
            return suffix[:240], "cp_one_line"
    return subject[:240], "cp_one_line"


def extract_closeout_note(subject: str, body: str) -> tuple[str, str]:
    """Derive cell note from CLOSEOUT subject + first body line."""
    head = subject
    if " — " in subject:
        head = subject.split(" — ", 1)[1].strip()
    first_line = ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    note = f"{head} — {first_line}" if first_line else head
    return note[:240], "closeout_head"


def prose_tail_for_range(facts: WindowFacts, turn_lo: int, turn_hi: int) -> str:
    """Last substantive assistant prose in (turn_lo, turn_hi], capped at 300 chars."""
    best = ""
    for turn, prose in facts.prose_by_turn.items():
        if turn_lo < turn <= turn_hi and len(prose) >= 120:
            best = prose
    return best[:300]


def detect_anchor_mismatches(
    root: str,
    *,
    bus_get: Callable[[str], Any] | None = None,
    member_facts: dict[str, WindowFacts],
) -> list[AnchorMismatch]:
    """Compare CP ``Window:`` lines on *root* against observed JSONL sends."""
    getter = bus_get or _default_bus_get
    turns = _turns_from_payload(getter(f"/turns?thread={root}"))
    mismatches: list[AnchorMismatch] = []
    cp_ordinal = 0
    for row in turns:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject") or "")
        if not subject.upper().startswith("CHECKPOINT"):
            continue
        cp_ordinal += 1
        body = str(row.get("body") or "")
        match = _WINDOW_ANCHOR_RE.search(body)
        if not match:
            continue
        claimed_uuid, claimed_count = match.group(1), int(match.group(2))
        cp_turn = int(row.get("turn_number") or 0)
        observed_uuid: str | None = None
        observed_turn: int | None = None
        for tid, wf in member_facts.items():
            for send in wf.bus_sends:
                if send.subject == subject and send.thread == root:
                    observed_uuid = tid
                    observed_turn = send.turn_index
                    break
            if observed_uuid:
                break
        if observed_uuid is None:
            mismatches.append(
                AnchorMismatch(
                    cp_turn=cp_turn,
                    cp_ordinal=cp_ordinal,
                    claimed={"transcript_id": claimed_uuid, "turns_at_cp": claimed_count},
                    observed={},
                    kind="unobserved",
                )
            )
            continue
        kind_parts: list[str] = []
        if observed_uuid != claimed_uuid:
            kind_parts.append("uuid")
        if observed_turn != claimed_count:
            kind_parts.append("count")
        if kind_parts:
            mismatches.append(
                AnchorMismatch(
                    cp_turn=cp_turn,
                    cp_ordinal=cp_ordinal,
                    claimed={"transcript_id": claimed_uuid, "turns_at_cp": claimed_count},
                    observed={
                        "transcript_id": observed_uuid,
                        "turn_index": observed_turn,
                    },
                    kind="+".join(kind_parts),
                )
            )
    return mismatches


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse thread created_at for mtime pre-filter."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format for state metadata."""
    return datetime.now(UTC).isoformat()


__all__ = [
    "AnchorMismatch",
    "BusTurnIndex",
    "MembershipResult",
    "boundary_sends_for_lane",
    "build_bus_turn_index",
    "classify_membership",
    "detect_anchor_mismatches",
    "extract_closeout_note",
    "extract_cp_highlight",
    "extract_cp_note",
    "HIGHLIGHT_MAX_CHARS",
    "fetch_lineage_children",
    "fetch_thread_detail",
    "join_boundary_turn_number",
    "jsonl_candidates",
    "parse_iso_datetime",
    "prose_tail_for_range",
]
