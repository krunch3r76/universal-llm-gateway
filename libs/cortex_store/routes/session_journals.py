from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status

from ..db import cortex_conn, decode_row, json_encode, query
from ..dispatch_ops._shared import record
from ..models import (
    SessionCloseRequest,
    SessionCloseResponse,
    SessionJournalCreate,
    SessionJournalItem,
    SessionJournalList,
)
from .reflective_journal import _insert_journal_link_tx, _insert_reflective_entry_tx

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
_USER_VOICE_RE = re.compile(r"\*\*User:\*\*|\bUser:\s|^#{1,4}\s+User\b", re.MULTILINE)

# Reason enum for mcp.session.close.rejected — see docs/event-contracts.md
_REJECT_REASONS = frozenset(
    {
        "transcript.hollow",
        "transcript.missing_structure",
        "summary.too_short",
        "session_id.invalid",
        "session.already_closed",
    }
)


def _emit_rejected(reason: str, *, session_id: str, agent: str, detail: str) -> None:
    """Emit mcp.session.close.rejected on every 422 reject path.

    Reason MUST be one of _REJECT_REASONS (enforced via assertion in dev).
    """
    assert reason in _REJECT_REASONS, f"unknown reject reason {reason!r}"
    record(
        "mcp.session.close.rejected",
        reason=reason,
        session_id=session_id,
        agent=agent,
        detail=detail,
    )


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
        detail = (
            f"session_id {body.session_id!r} does not match "
            "pattern {{agent}}-YYYY-MM-DD-HHMM"
        )
        _emit_rejected(
            "session_id.invalid",
            session_id=body.session_id,
            agent=body.agent,
            detail=detail,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )

    if len(body.summary) < 20:
        detail = f"summary must be >= 20 characters (got {len(body.summary)})"
        _emit_rejected(
            "summary.too_short",
            session_id=body.session_id,
            agent=body.agent,
            detail=detail,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )

    if len(body.transcript_md) < 200:
        detail = (
            f"transcript_md must be >= 200 characters (got {len(body.transcript_md)}). "
            "Stub-only closes are rejected."
        )
        _emit_rejected(
            "transcript.missing_structure",
            session_id=body.session_id,
            agent=body.agent,
            detail=detail,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )

    has_structure = (
        "## Turn" in body.transcript_md or "## Session Summary" in body.transcript_md
    )
    if not has_structure:
        detail = (
            "transcript_md must contain at least one '## Turn' heading "
            "or a '## Session Summary' section."
        )
        _emit_rejected(
            "transcript.missing_structure",
            session_id=body.session_id,
            agent=body.agent,
            detail=detail,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )

    # Dual-layer doctrine — verbatim layer must be present alongside structural.
    # File with `## Turn` headings but zero User-voice blocks is the
    # web-2026-05-03-0431 failure mode (structural ✓, verbatim ✗).
    # See agent-skills/web-session-close.md Step 2 for the dual-layer doctrine
    # and notes/system/shared/session-close-protocol.md Step 2 for the canon.
    user_blocks = len(_USER_VOICE_RE.findall(body.transcript_md))
    if user_blocks == 0:
        detail = (
            "transcript_md has structural headings but zero User-voice blocks "
            "(`**User:**` / `User:` / `### User`). This is the dual-layer "
            "doctrine failure: structural layer present, verbatim layer hollow. "
            "Rewrite mechanically — copy each user message verbatim into a "
            "`### User` block. See agent-skills/web-session-close.md Step 2."
        )
        _emit_rejected(
            "transcript.hollow",
            session_id=body.session_id,
            agent=body.agent,
            detail=detail,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )

    # ── derive values ──
    transcript_entity_id = f"transcript:{body.session_id}"
    transcript_path = f"notes/system/transcripts/{body.session_id}.md"
    source_uri = f"files://{transcript_path}"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    opened_at = _parse_opened_at(body.session_id)

    # ── idempotency gate: reject duplicate close for an already-closed session ──
    # Pre-034 retries silently produced duplicate journal rows. Migration 034
    # enforces UNIQUE(session_id); this check turns the IntegrityError into a
    # structured 422 carrying the existing IDs so the caller can recover without
    # reading them back.
    existing = (
        cortex_conn()
        .execute(
            "SELECT id, file_path FROM session_journals WHERE session_id = ?",
            (body.session_id,),
        )
        .fetchone()
    )
    if existing is not None:
        already_detail = {
            "reason": "session.already_closed",
            "session_id": body.session_id,
            "transcript_entity_id": transcript_entity_id,
            "transcript_path": existing["file_path"] or transcript_path,
            "journal_row_id": existing["id"],
            "message": (
                f"session_close: {body.session_id} is already closed "
                f"(journal_row_id={existing['id']}). The previous close is "
                "the source of truth — do not retry; treat this as success."
            ),
        }
        _emit_rejected(
            "session.already_closed",
            session_id=body.session_id,
            agent=body.agent,
            detail=already_detail["message"],
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=already_detail,
        )

    handoff_prompt = body.handoff_prompt.strip() if body.handoff_prompt else None

    # ── derive entity name from summary (first ~6 words) ──
    name_words = body.summary.split()[:6]
    entity_name = " ".join(name_words)
    if len(name_words) < len(body.summary.split()):
        entity_name += "…"

    conn = cortex_conn()
    handoff_entry_id: int | None = None
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

        # 4. Optionally create handoff reflective entry + link
        if handoff_prompt:
            handoff_entry_id = _insert_reflective_entry_tx(
                conn,
                agent=body.agent,
                register="self",
                entry=handoff_prompt,
                kind="handoff",
                session_id=body.session_id,
            )
            _insert_journal_link_tx(
                conn,
                from_entry=handoff_entry_id,
                to_entity=transcript_entity_id,
                link_type="handoff_for",
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
        handoff_entry_id=handoff_entry_id,
        transcript_path=transcript_path,
        journal_row_id=journal_row_id,
        session_id=body.session_id,
    )


def _list_session_journals_impl(
    *, agent: str | None = None, limit: int = 3
) -> dict[str, object]:
    return list_session_journals(agent=agent, limit=limit).model_dump(mode="json")


def _create_session_journal_impl(payload: dict[str, object]) -> dict[str, object]:
    data = create_session_journal(SessionJournalCreate.model_validate(payload))
    return data.model_dump(mode="json")


def _close_session_impl(payload: dict[str, object]) -> dict[str, object]:
    data = close_session(SessionCloseRequest.model_validate(payload))
    return data.model_dump(mode="json")
