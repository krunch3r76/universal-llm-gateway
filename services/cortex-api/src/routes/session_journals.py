from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.db import cortex_conn, decode_row, json_encode, query
from src.models import SessionJournalCreate, SessionJournalItem, SessionJournalList

logger = logging.getLogger("cortex-api.session_journals")
router = APIRouter(prefix="/session-journals", tags=["session-journals"])

_JSON_FIELDS = frozenset({"domains", "decisions", "open_items"})


@router.get("", response_model=SessionJournalList)
def list_session_journals(
    limit: int = Query(3, ge=1, le=100),
) -> SessionJournalList:
    """List recent session journals in reverse insertion order."""
    conn = cortex_conn()
    try:
        rows = query(
            conn,
            "SELECT * FROM session_journals ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    finally:
        conn.close()

    return SessionJournalList(
        items=[SessionJournalItem(**decode_row(row, _JSON_FIELDS)) for row in rows]
    )


@router.post("", response_model=SessionJournalItem, status_code=status.HTTP_201_CREATED)
def create_session_journal(body: SessionJournalCreate) -> SessionJournalItem:
    """Create a session journal row and return the inserted item."""
    conn = cortex_conn()
    try:
        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, domains, decisions, open_items, file_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                body.timestamp,
                body.agent,
                body.summary,
                json_encode(body.domains),
                json_encode(body.decisions),
                json_encode(body.open_items),
                body.file_path,
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
    return SessionJournalItem(**decode_row(rows[0], _JSON_FIELDS))
