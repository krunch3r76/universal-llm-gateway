"""Join CHECKPOINT turns with session journals for ``## Windows`` render-on-read."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from cortex_store.transcript_cp_anchors import window_anchors_from_text

from .checkpoint_projection import CHECKPOINT_SUBJECT_SQL

_ARC_PREFIX_RE = re.compile(r"^arc:\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CheckpointTurnRow:
    turn_number: int
    cp_ordinal: int
    created_at: str
    subject: str
    body: str = ""


@dataclass(frozen=True, slots=True)
class WindowRow:
    cp_ordinal: int
    turn: int
    session_id: str | None
    arc: str | None
    journal_row_id: int | None


class JournalFetcher(Protocol):
    def __call__(self, *, thread_id: str) -> tuple[dict[str, Any], ...]: ...


class CheckpointTurnLister(Protocol):
    def __call__(self, *, thread_id: str) -> tuple[CheckpointTurnRow, ...]: ...


def timestamp_to_utc_instant(raw: str) -> datetime | None:
    """Normalize bus or journal timestamp strings to a comparable UTC instant."""
    text = raw.strip()
    if not text:
        return None
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    from cortex_store.session_close_successor_hop import parse_utc_timestamp

    return parse_utc_timestamp(text)


def extract_arc_from_summary(summary: str) -> str | None:
    """Return the ``Arc:`` one-liner from a session journal summary, if present."""
    for line in summary.splitlines():
        stripped = line.strip()
        if _ARC_PREFIX_RE.match(stripped):
            return _ARC_PREFIX_RE.sub("", stripped).strip() or None
    return None


def journal_cites_thread(*, entity_ids: list[str] | None, thread_id: str) -> bool:
    from cortex_store.dispatch_ops._session_bus_thread_disposition import (
        parse_bus_thread_refs,
    )

    return thread_id in parse_bus_thread_refs(entity_ids)


def list_checkpoint_turns(*, thread_id: str) -> tuple[CheckpointTurnRow, ...]:
    """Return CHECKPOINT turns on *thread_id* in turn_number order."""
    from .db.connection import connect

    with connect() as conn:
        rows = conn.execute(
            f"SELECT turn_number, subject, created_at, body FROM turns "
            f"WHERE thread = ? AND {CHECKPOINT_SUBJECT_SQL} "
            f"ORDER BY turn_number ASC",
            (thread_id,),
        ).fetchall()
    out: list[CheckpointTurnRow] = []
    for ordinal, row in enumerate(rows, start=1):
        out.append(
            CheckpointTurnRow(
                turn_number=int(row["turn_number"]),
                cp_ordinal=ordinal,
                created_at=str(row["created_at"]),
                subject=str(row["subject"]),
                body=str(row["body"] or ""),
            )
        )
    return tuple(out)


def compute_journal_fetch_limit(*, checkpoint_turn_count: int) -> int:
    """Bound journal fetch for window join; keeps newest rows when truncated."""
    return max(checkpoint_turn_count * 4, 32)


def fetch_journals_for_thread(
    *,
    thread_id: str,
    limit: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return session journal rows whose ``entity_ids`` cite *thread_id*."""
    from cortex_store.db import cortex_conn, decode_row, query

    json_fields = frozenset({"domains", "decisions", "open_items", "entity_ids"})
    agent_bus_ref = f"agent-bus:{thread_id}"
    order = "ORDER BY id DESC" if limit is not None else "ORDER BY id ASC"
    sql = (
        "SELECT * FROM session_journals "
        "WHERE entity_ids IS NOT NULL "
        "AND ("
        "  EXISTS (SELECT 1 FROM json_each(entity_ids) WHERE value = ?) "
        "  OR EXISTS (SELECT 1 FROM json_each(entity_ids) WHERE value = ?)"
        ") "
        f"{order}"
    )
    params: list[Any] = [agent_bus_ref, thread_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))
    decoded = tuple(decode_row(row, json_fields) for row in rows)
    if limit is not None:
        decoded = tuple(sorted(decoded, key=lambda row: int(row["id"])))
    return decoded


def fetch_journals_for_checkpoint_windows(
    *,
    thread_id: str,
    checkpoint_turns: tuple[CheckpointTurnRow, ...],
) -> tuple[dict[str, Any], ...]:
    """Fetch journals with the shared limit policy for all render entry points."""
    limit = compute_journal_fetch_limit(checkpoint_turn_count=len(checkpoint_turns))
    return fetch_journals_for_thread(thread_id=thread_id, limit=limit)


def _lookup_window_by_uuid(conversation_uuid: str) -> dict[str, Any] | None:
    from cortex_store.db import cortex_conn, query

    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, session_id, summary, closed_by FROM session_windows "
            "WHERE conversation_uuid = ? ORDER BY id DESC LIMIT 1",
            (conversation_uuid,),
        )
    return rows[0] if rows else None


def _window_row_from_uuid_anchor(
    *,
    cp: CheckpointTurnRow,
    conversation_uuid: str,
) -> WindowRow:
    journal = _lookup_window_by_uuid(conversation_uuid)
    if journal is None:
        return WindowRow(
            cp_ordinal=cp.cp_ordinal,
            turn=cp.turn_number,
            session_id=None,
            arc=None,
            journal_row_id=None,
        )
    summary = str(journal.get("summary") or "")
    session_id = journal.get("session_id")
    if isinstance(session_id, str):
        session_id = session_id.strip() or None
    else:
        session_id = None
    closed_by = journal.get("closed_by")
    journal_row_id: int | None = None
    if closed_by and closed_by != "succession" and journal.get("id") is not None:
        journal_row_id = int(journal["id"])
    return WindowRow(
        cp_ordinal=cp.cp_ordinal,
        turn=cp.turn_number,
        session_id=session_id,
        arc=extract_arc_from_summary(summary),
        journal_row_id=journal_row_id,
    )


def _window_row_from_interval_join(
    *,
    cp: CheckpointTurnRow,
    idx: int,
    checkpoint_turns: tuple[CheckpointTurnRow, ...],
    sorted_journals: tuple[dict[str, Any], ...],
) -> WindowRow:
    window_start = timestamp_to_utc_instant(cp.created_at)
    window_end = (
        timestamp_to_utc_instant(checkpoint_turns[idx + 1].created_at)
        if idx + 1 < len(checkpoint_turns)
        else None
    )
    if window_start is None:
        window_start = datetime.min.replace(tzinfo=UTC)
    matched: list[dict[str, Any]] = []
    for journal in sorted_journals:
        ts = timestamp_to_utc_instant(str(journal.get("timestamp") or ""))
        if ts is None:
            continue
        if ts < window_start:
            continue
        if window_end is not None and ts >= window_end:
            continue
        matched.append(journal)
    journal = matched[-1] if matched else None
    arc = None
    journal_row_id = None
    session_id = None
    if journal is not None:
        summary = str(journal.get("summary") or "")
        arc = extract_arc_from_summary(summary)
        journal_row_id = int(journal["id"]) if journal.get("id") is not None else None
        session_id = journal.get("session_id")
        if isinstance(session_id, str):
            session_id = session_id.strip() or None
        else:
            session_id = None
    return WindowRow(
        cp_ordinal=cp.cp_ordinal,
        turn=cp.turn_number,
        session_id=session_id,
        arc=arc,
        journal_row_id=journal_row_id,
    )


def join_windows(
    *,
    checkpoint_turns: tuple[CheckpointTurnRow, ...],
    journals: tuple[dict[str, Any], ...],
) -> tuple[WindowRow, ...]:
    """Pair each CHECKPOINT window with a journal row (uuid join or interval fallback)."""
    if not checkpoint_turns:
        return ()

    sorted_journals = tuple(
        sorted(
            journals,
            key=lambda j: timestamp_to_utc_instant(str(j.get("timestamp") or ""))
            or datetime.min.replace(tzinfo=UTC),
        )
    )
    out: list[WindowRow] = []
    for idx, cp in enumerate(checkpoint_turns):
        anchors = window_anchors_from_text(cp.body)
        if anchors:
            uuid, _turns_at_cp = anchors[-1]
            out.append(
                _window_row_from_uuid_anchor(cp=cp, conversation_uuid=uuid)
            )
            continue
        out.append(
            _window_row_from_interval_join(
                cp=cp,
                idx=idx,
                checkpoint_turns=checkpoint_turns,
                sorted_journals=sorted_journals,
            )
        )
    return tuple(out)


__all__ = [
    "CheckpointTurnLister",
    "CheckpointTurnRow",
    "JournalFetcher",
    "WindowRow",
    "compute_journal_fetch_limit",
    "extract_arc_from_summary",
    "fetch_journals_for_checkpoint_windows",
    "fetch_journals_for_thread",
    "join_windows",
    "journal_cites_thread",
    "list_checkpoint_turns",
    "timestamp_to_utc_instant",
]
