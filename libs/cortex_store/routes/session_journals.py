from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status

from ..db import cortex_conn, decode_row, json_encode, query
from ..models import (
    SessionCloseRequest,
    SessionCloseResponse,
    SessionJournalCreate,
    SessionJournalItem,
    SessionJournalList,
)

logger = logging.getLogger("cortex-api.session_journals")
router = APIRouter(prefix="/session-journals", tags=["session-journals"])

_JSON_FIELDS = frozenset({"domains", "decisions", "open_items", "entity_ids"})


def _parse_opened_at(transcript_id: str) -> str | None:
    """Derive ``opened_at`` ISO 8601 from transcript ID (``{agent}-YYYY-MM-DD-HHMM``)."""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})$", transcript_id)
    if match:
        y, m, d, hh, mm = match.groups()
        return f"{y}-{m}-{d}T{hh}:{mm}:00Z"
    return None


def _stamp_transcript_timestamps(
    conn: object,
    transcript_id: str,
    *,
    opened_at: str | None = None,
    closed_at: str | None = None,
) -> None:
    """Merge ``opened_at`` / ``closed_at`` into the transcript entity's attributes."""
    if not opened_at and not closed_at:
        return
    entity_id = f"transcript:{transcript_id}"
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT attributes FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    existing: dict[str, object] = (
        json.loads(row["attributes"]) if row and row["attributes"] else {}
    )
    if opened_at:
        existing["opened_at"] = opened_at
    if closed_at:
        existing["closed_at"] = closed_at
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(  # type: ignore[union-attr]
        "UPDATE entities SET attributes = ?, updated_at = ? WHERE id = ?",
        (json_encode(existing), now, entity_id),
    )


def _derive_session_id(agent: str, timestamp: str) -> str:
    """Derive a session ID from agent + timestamp string.

    Accepts ISO 8601 (``2026-04-08T23:11:00Z``) or any string containing
    a ``YYYY-MM-DD`` date fragment.  Falls back to today if unparseable.
    Format: ``{agent}-YYYY-MM-DD-HHMM``.
    """
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):?(\d{2})", timestamp)
    if match:
        year, mon, day, hour, minute = match.groups()
        return f"{agent}-{year}-{mon}-{day}-{hour}{minute}"
    today = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
    return f"{agent}-{today}"


def _ensure_transcript_entity(
    conn: object,
    transcript_id: str,
    agent: str,
    timestamp: str,
    source_uri: str | None = None,
) -> None:
    """INSERT OR IGNORE the transcript entity — idempotent.

    When *source_uri* is provided, also updates it on an existing entity
    (INSERT OR IGNORE won't overwrite an existing row).
    """
    entity_id = f"transcript:{transcript_id}"
    if source_uri is None:
        source_uri = f"files://notes/system/transcripts/{transcript_id}.md"
    conn.execute(  # type: ignore[union-attr]
        "INSERT OR IGNORE INTO entities "
        "(id, type, name, status, source_uri, created_at, updated_at) "
        "VALUES (?, 'transcript', ?, 'confirmed', ?, ?, ?)",
        (entity_id, transcript_id, source_uri, timestamp, timestamp),
    )
    conn.execute(  # type: ignore[union-attr]
        "UPDATE entities SET source_uri = ?, updated_at = ? "
        "WHERE id = ? AND (source_uri IS NULL OR source_uri != ?)",
        (source_uri, timestamp, entity_id, source_uri),
    )


def _ensure_continues_edge(
    conn: object,
    session_id: str,
    prior_session_id: str,
    agent: str,
    timestamp: str,
) -> None:
    """Write a continues edge: transcript:{session_id} → transcript:{prior_session_id}.

    Checks for an existing active edge before inserting to stay idempotent.
    """
    from_node = f"transcript:{session_id}"
    to_node = f"transcript:{prior_session_id}"
    existing = conn.execute(  # type: ignore[union-attr]
        "SELECT 1 FROM session_edges "
        "WHERE from_node = ? AND to_node = ? AND edge_type = 'continues' "
        "AND valid_until IS NULL LIMIT 1",
        (from_node, to_node),
    ).fetchone()
    if existing:
        return
    conn.execute(  # type: ignore[union-attr]
        "INSERT INTO session_edges "
        "(session_id, agent, from_node, to_node, edge_type, strength, edge_source, created_at) "
        "VALUES (?, ?, ?, ?, 'continues', 0.9, 'server', ?)",
        (session_id, agent, from_node, to_node, timestamp),
    )


@router.get("", response_model=SessionJournalList)
def list_session_journals(
    agent: str | None = None,
    limit: int = Query(3, ge=1, le=100),
) -> SessionJournalList:
    """List recent session journals in reverse insertion order.

    Args:
        agent: Filter by agent name (cursor, web, api). Omit for all agents.
        limit: Maximum results (1-100, default 3).
    """
    clauses: list[str] = []
    params: list[str | int] = []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    conn = cortex_conn()
    try:
        rows = query(
            conn,
            f"SELECT * FROM session_journals{where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
    finally:
        conn.close()

    return SessionJournalList(
        items=[SessionJournalItem(**decode_row(row, _JSON_FIELDS)) for row in rows]
    )


@router.post("", response_model=SessionJournalItem, status_code=status.HTTP_201_CREATED)
def create_session_journal(body: SessionJournalCreate) -> SessionJournalItem:
    """Create a session journal row, auto-create transcript entity, and return the item.

    Side effects (all within the same transaction):
    - transcript:{session_id} entity is created (INSERT OR IGNORE — idempotent)
    - A ``continues`` edge is written if ``prior_session_id`` is supplied
    ``session_id`` is derived from the timestamp if not explicitly provided.
    """
    transcript_id = body.session_id or _derive_session_id(body.agent, body.timestamp)
    transcript_entity_id = f"transcript:{transcript_id}"

    conn = cortex_conn()
    try:
        _ensure_transcript_entity(conn, transcript_id, body.agent, body.timestamp)
        _stamp_transcript_timestamps(
            conn,
            transcript_id,
            opened_at=_parse_opened_at(transcript_id),
            closed_at=body.timestamp,
        )

        if body.prior_session_id:
            # Ensure the prior transcript entity exists too (so the FK-like
            # reference is safe even when the prior session journal isn't present).
            _ensure_transcript_entity(
                conn, body.prior_session_id, body.agent, body.timestamp
            )
            _ensure_continues_edge(
                conn, transcript_id, body.prior_session_id, body.agent, body.timestamp
            )

        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, domains, decisions, open_items, "
            "entity_ids, file_path, session_id, prior_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.timestamp,
                body.agent,
                body.summary,
                json_encode(body.domains),
                json_encode(body.decisions),
                json_encode(body.open_items),
                json_encode(body.entity_ids),
                body.file_path,
                transcript_id,
                body.prior_session_id,
            ),
        )
        conn.commit()
        rows = query(
            conn,
            "SELECT * FROM session_journals WHERE id = ?",
            (cur.lastrowid,),
        )
    finally:
        conn.close()

    if not rows:
        logger.error(
            "Session journal create succeeded but no row returned for agent=%s",
            body.agent,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session journal created but could not be read back",
        )

    item = SessionJournalItem(**decode_row(rows[0], _JSON_FIELDS))
    item.transcript_entity_id = transcript_entity_id
    return item


_SESSION_ID_RE = re.compile(r"^[a-z]+-\d{4}-\d{2}-\d{2}-\d{4}$")


@router.post(
    "/close",
    response_model=SessionCloseResponse,
    status_code=status.HTTP_201_CREATED,
)
def close_session(body: SessionCloseRequest) -> SessionCloseResponse:
    """Atomic session close: create transcript entity, journal row, and continues edge.

    The MCP dispatch layer writes the transcript file to disk before calling
    this endpoint.  This handler owns the DB-side atomicity guarantee.

    Validation (server-enforced):
    - session_id matches ``{agent}-YYYY-MM-DD-HHMM``
    - summary >= 20 characters
    - transcript_md >= 200 characters with structural headings
    """
    # ── validation ──
    if not _SESSION_ID_RE.match(body.session_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"session_id {body.session_id!r} does not match "
                "pattern {{agent}}-YYYY-MM-DD-HHMM"
            ),
        )

    if len(body.summary) < 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"summary must be >= 20 characters (got {len(body.summary)})",
        )

    if len(body.transcript_md) < 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"transcript_md must be >= 200 characters (got {len(body.transcript_md)}). "
                "Stub-only closes are rejected."
            ),
        )

    has_structure = (
        "## Turn" in body.transcript_md or "## Session Summary" in body.transcript_md
    )
    if not has_structure:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "transcript_md must contain at least one '## Turn' heading "
                "or a '## Session Summary' section."
            ),
        )

    # ── derive values ──
    transcript_entity_id = f"transcript:{body.session_id}"
    transcript_path = f"notes/system/transcripts/{body.session_id}.md"
    source_uri = f"files://{transcript_path}"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    opened_at = _parse_opened_at(body.session_id)

    # ── derive entity name from summary (first ~6 words) ──
    name_words = body.summary.split()[:6]
    entity_name = " ".join(name_words)
    if len(name_words) < len(body.summary.split()):
        entity_name += "…"

    conn = cortex_conn()
    try:
        # 1. Create transcript entity
        conn.execute(
            "INSERT OR IGNORE INTO entities "
            "(id, type, name, description, status, source_uri, attributes, "
            "created_at, updated_at) "
            "VALUES (?, 'transcript', ?, ?, 'confirmed', ?, ?, ?, ?)",
            (
                transcript_entity_id,
                entity_name,
                body.summary,
                source_uri,
                json_encode(
                    {
                        "opened_at": opened_at,
                        "closed_at": now,
                        "status": "confirmed",
                    }
                ),
                now,
                now,
            ),
        )

        # 2. Create thin journal index row
        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, domains, decisions, open_items, "
            "entity_ids, file_path, session_id, prior_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now,
                body.agent,
                body.summary,
                json_encode(body.domains),
                json_encode(body.decisions),
                json_encode(body.open_items),
                json_encode(body.entity_ids),
                transcript_path,
                body.session_id,
                body.prior_session_id,
            ),
        )
        journal_row_id = cur.lastrowid or 0

        # 3. Create continues edge if chained
        if body.prior_session_id:
            _ensure_transcript_entity(conn, body.prior_session_id, body.agent, now)
            _ensure_continues_edge(
                conn, body.session_id, body.prior_session_id, body.agent, now
            )

        conn.commit()
    except Exception:
        conn.rollback()
        logger.error(
            "session_close DB transaction failed for %s",
            body.session_id,
            exc_info=True,
        )
        raise
    finally:
        conn.close()

    logger.info(
        "session_close: %s agent=%s entity=%s journal_row=%d",
        body.session_id,
        body.agent,
        transcript_entity_id,
        journal_row_id,
    )

    return SessionCloseResponse(
        transcript_entity_id=transcript_entity_id,
        transcript_path=transcript_path,
        journal_row_id=journal_row_id,
        session_id=body.session_id,
    )
