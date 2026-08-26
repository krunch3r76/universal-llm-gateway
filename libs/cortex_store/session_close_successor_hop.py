"""Successor-hop for a second (Nth) ``/session-end`` in the same tab.

``UNIQUE(session_id)`` stays. Persist never auto-hops: a sealed id still
echoes ``already_closed``. When preflight sees that id already journaled
*and* a later JSONL user ``<timestamp>``, it mints a successor and returns
copy-paste ``session_id`` / ``prior_session_id``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_seat.session_id import derive_session_id_from_timestamp, mint_session_id

from .transcript_session_id import _JSONL_TIMESTAMP_TAG_RE, _normalize_cursor_timestamp

HOP_REASON = "session_id_already_journaled"
_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):?(\d{2})(?::(\d{2}))?")
_CHAIN_WALK_CAP = 32


@dataclass(frozen=True)
class SealedJournal:
    """One ``session_journals`` row used as a hop anchor or chain tip."""

    session_id: str
    journal_row_id: int
    timestamp: str
    prior_session_id: str | None


@dataclass(frozen=True)
class SuccessorHop:
    """Copy-paste fields preflight returns when a hop applies."""

    session_id: str
    prior_session_id: str
    hop_reason: str = HOP_REASON


def conversation_uuid_from_jsonl_path(jsonl_path: Path) -> str:
    """Cursor layout is ``<conversation-uuid>/<conversation-uuid>.jsonl``."""
    return jsonl_path.parent.name


def parse_utc_timestamp(raw: str) -> datetime | None:
    """Parse a journal or JSONL ``<timestamp>`` into an aware UTC datetime."""
    text = _normalize_cursor_timestamp(raw.strip())
    match = _TS_RE.search(text)
    if not match:
        return None
    year, mon, day, hour, minute, second = match.groups()
    return datetime(
        int(year),
        int(mon),
        int(day),
        int(hour),
        int(minute),
        int(second or "0"),
        tzinfo=UTC,
    )


def lookup_sealed_journal(session_id: str) -> SealedJournal | None:
    """Return the journal row for *session_id*, or ``None`` if unsealed."""
    from .db import cortex_conn

    conn = cortex_conn()
    try:
        row = conn.execute(
            "SELECT id, session_id, timestamp, prior_session_id "
            "FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["session_id"]:
        return None
    return SealedJournal(
        session_id=str(row["session_id"]),
        journal_row_id=int(row["id"]),
        timestamp=str(row["timestamp"]),
        prior_session_id=row["prior_session_id"],
    )


def _lookup_child_journal(prior_session_id: str) -> SealedJournal | None:
    """Latest journal that lists *prior_session_id* as its predecessor."""
    from .db import cortex_conn

    conn = cortex_conn()
    try:
        row = conn.execute(
            "SELECT id, session_id, timestamp, prior_session_id "
            "FROM session_journals WHERE prior_session_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (prior_session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["session_id"]:
        return None
    return SealedJournal(
        session_id=str(row["session_id"]),
        journal_row_id=int(row["id"]),
        timestamp=str(row["timestamp"]),
        prior_session_id=row["prior_session_id"],
    )


def latest_journal_in_chain(session_id: str) -> SealedJournal | None:
    """Walk ``prior_session_id`` children to the tip of this tab's lids."""
    row = lookup_sealed_journal(session_id)
    if row is None:
        return None
    seen: set[str] = {row.session_id}
    for _ in range(_CHAIN_WALK_CAP):
        child = _lookup_child_journal(row.session_id)
        if child is None or child.session_id in seen:
            return row
        seen.add(child.session_id)
        row = child
    return row


def iter_jsonl_user_timestamps(jsonl_path: Path) -> list[datetime]:
    """Parse ``<timestamp>`` tags on JSONL user text turns, in file order."""
    from .transcript_assembly import _extract_user_text, _read_jsonl

    try:
        records = _read_jsonl(jsonl_path)
    except (OSError, ValueError):
        return []
    stamps: list[datetime] = []
    for record in records:
        if record.get("role") != "user":
            continue
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        user_text = _extract_user_text(content)
        if not user_text:
            continue
        match = _JSONL_TIMESTAMP_TAG_RE.search(user_text)
        if not match:
            continue
        parsed = parse_utc_timestamp(match.group(1))
        if parsed is not None:
            stamps.append(parsed)
    return stamps


def first_user_timestamp_after(jsonl_path: Path, after: datetime) -> datetime | None:
    """First user-turn timestamp strictly after *after*, or ``None``."""
    for stamp in iter_jsonl_user_timestamps(jsonl_path):
        if stamp > after:
            return stamp
    return None


def mint_successor_session_id(
    *,
    agent: str,
    at: datetime | None,
    conversation_uuid: str,
) -> str:
    """Mint the hop ``session_id`` from the first post-lid user timestamp.

    When *at* is missing or unparseable, live-mint a random suffix. Never
    ``st_mtime``. Never wall-clock ``date -u`` at the caller.
    """
    if at is None:
        return mint_session_id(agent)
    return derive_session_id_from_timestamp(
        agent,
        at.strftime("%Y-%m-%d %H:%M:%S"),
        deterministic=True,
        conversation_uuid=conversation_uuid,
    )


def resolve_successor_hop(
    *,
    supplied_session_id: str,
    jsonl_start_id: str | None,
    jsonl_path: Path | None,
    agent: str,
) -> SuccessorHop | None:
    """Hop when a candidate id is sealed and JSONL has later user work.

    Supplied (boot-held) is checked first. A sealed supplied id with no
    later user turn is ``already_closed`` — do not hop from jsonl-start.
    jsonl-start is checked only when supplied is not journaled.
    """
    if jsonl_path is None:
        return None
    anchors = [supplied_session_id]
    if jsonl_start_id and jsonl_start_id != supplied_session_id:
        anchors.append(jsonl_start_id)
    for index, anchor in enumerate(anchors):
        tip = latest_journal_in_chain(anchor)
        if tip is None:
            continue
        after = parse_utc_timestamp(tip.timestamp)
        if after is None:
            continue
        first = first_user_timestamp_after(jsonl_path, after)
        if first is None:
            if index == 0:
                return None
            continue
        successor = mint_successor_session_id(
            agent=agent,
            at=first,
            conversation_uuid=conversation_uuid_from_jsonl_path(jsonl_path),
        )
        return SuccessorHop(session_id=successor, prior_session_id=tip.session_id)
    return None


def apply_successor_hop_fields(
    preflight: dict[str, Any],
    *,
    supplied_session_id: str,
    agent: str,
    transcript_jsonl_path: str | None,
    jsonl_resolved: Path | None,
) -> None:
    """Mutate a successful preflight dict with hop copy-paste fields."""
    if not preflight.get("ok"):
        return
    path = jsonl_resolved
    if path is None and transcript_jsonl_path:
        from .transcript_assembly import TranscriptPathError, resolve_jsonl_path

        try:
            path = resolve_jsonl_path(transcript_jsonl_path)
        except TranscriptPathError:
            path = None
    hop = resolve_successor_hop(
        supplied_session_id=supplied_session_id,
        jsonl_start_id=preflight.get("session_id_from_jsonl_start"),
        jsonl_path=path,
        agent=agent,
    )
    if hop is None:
        return
    preflight["session_id"] = hop.session_id
    preflight["prior_session_id"] = hop.prior_session_id
    preflight["hop_reason"] = hop.hop_reason
    from .dispatch_ops._session_summary_path import summary_path_hint

    preflight["summary_path_hint"] = summary_path_hint(session_id=hop.session_id)


__all__ = [
    "HOP_REASON",
    "SealedJournal",
    "SuccessorHop",
    "apply_successor_hop_fields",
    "conversation_uuid_from_jsonl_path",
    "first_user_timestamp_after",
    "iter_jsonl_user_timestamps",
    "latest_journal_in_chain",
    "lookup_sealed_journal",
    "mint_successor_session_id",
    "parse_utc_timestamp",
    "resolve_successor_hop",
]
