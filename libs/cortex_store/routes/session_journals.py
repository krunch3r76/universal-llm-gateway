from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from universal_logging import get_logger

from ..db import cortex_conn, decode_row, json_encode, query
from ..dispatch_ops._shared import _FILES_ROOT  # re-export for tests / dispatch parity
from ..models import (
    SessionCloseRequest,
    SessionCloseResponse,
    SessionJournalCreate,
    SessionJournalItem,
    SessionJournalList,
)
from .session_close import close_session
from .session_close_helpers import (
    _derive_session_id,
    _ensure_continues_edge,
    _ensure_transcript_entity,
    _parse_opened_at,
    _stamp_transcript_timestamps,
)

logger = get_logger("cortex-api.session_journals")
router = APIRouter(prefix="/session-journals", tags=["session-journals"])

_JSON_FIELDS = frozenset({"domains", "decisions", "open_items", "entity_ids"})


@router.get("", response_model=SessionJournalList)
def list_session_journals(
    agent: str | None = None,
    limit: int = Query(3, ge=1, le=100),
) -> SessionJournalList:
    """List recent session journals in reverse insertion order."""
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
    """Create a session journal row, auto-create transcript entity, and return the item."""
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
            _ensure_transcript_entity(
                conn, body.prior_session_id, body.agent, body.timestamp
            )
            _ensure_continues_edge(
                conn, transcript_id, body.prior_session_id, body.agent, body.timestamp
            )

        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, domains, decisions, open_items, "
            "entity_ids, file_path, session_id, prior_session_id, handoff_prompt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                body.handoff_prompt,
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


@router.post(
    "/close",
    response_model=SessionCloseResponse,
    status_code=status.HTTP_201_CREATED,
)
def close_session_route(body: SessionCloseRequest) -> SessionCloseResponse:
    """Atomic session close — handler in ``session_close``."""
    return close_session(body)


def _list_session_journals_impl(
    *, agent: str | None = None, limit: int = 3
) -> dict[str, object]:
    return list_session_journals(agent=agent, limit=limit).model_dump(mode="json")


def _create_session_journal_impl(payload: dict[str, object]) -> dict[str, object]:
    data = create_session_journal(SessionJournalCreate.model_validate(payload))
    return data.model_dump(mode="json")


def _close_session_impl(payload: dict[str, object]) -> dict[str, object]:
    from ..dispatch_ops._session_summary_path import resolve_session_summary_md
    from fastapi import HTTPException

    md = payload.get("session_summary_md")
    path = payload.get("session_summary_md_path")
    md_s = md if isinstance(md, str) else None
    path_s = path if isinstance(path, str) else None
    if path_s:
        resolved, path_err = resolve_session_summary_md(
            session_summary_md=md_s,
            session_summary_md_path=path_s,
        )
        if path_err is not None:
            raise HTTPException(status_code=422, detail=path_err)
        body_payload = {**payload, "session_summary_md": resolved}
    else:
        body_payload = payload
    data = close_session(SessionCloseRequest.model_validate(body_payload))
    return data.model_dump(mode="json")
