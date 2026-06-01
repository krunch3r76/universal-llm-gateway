"""POST /session-journals/{session_id}/handoff — post-close handoff upsert."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from universal_logging import get_logger

from ..db import cortex_conn
from ..dispatch_ops._shared import (
    _SESSION_ID_EXAMPLES,
    _SESSION_ID_RE,
    _SESSION_ID_RE_SOURCE,
    record,
)
from ..models import SessionHandoffUpsertRequest, SessionHandoffUpsertResponse
from ..session_close_validation import build_validation_error
from ..session_handoff import (
    mirror_handoff_to_transcript_entity,
    require_closed_journal_row,
)

logger = get_logger("cortex-api.session_handoff")
router = APIRouter(prefix="/session-journals", tags=["session-journals"])


@router.post(
    "/{session_id}/handoff",
    response_model=SessionHandoffUpsertResponse,
    status_code=status.HTTP_200_OK,
)
def upsert_session_handoff(
    session_id: str,
    body: SessionHandoffUpsertRequest,
) -> SessionHandoffUpsertResponse:
    """Upsert the handoff prompt on an already-closed session.

    Writes/replaces ``handoff_prompt`` on the journal row and mirrors it to
    ``transcript:{session_id}`` entity attributes when that entity exists.
    At most one handoff per ``session_id`` (upsert, not append).
    """
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_validation_error(
                reason="session_id.invalid",
                field="session_id",
                received=session_id,
                expected=_SESSION_ID_RE_SOURCE,
                examples=_SESSION_ID_EXAMPLES,
                hint=(
                    "Agent slugs may contain hyphens — the full slug must "
                    "precede the YYYY-MM-DD-HHMM timestamp."
                ),
                detail=(
                    f"session_id {session_id!r} does not match pattern "
                    f"{_SESSION_ID_RE_SOURCE}."
                ),
            ),
        )

    handoff_prompt = body.handoff_prompt.strip()
    if not handoff_prompt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_validation_error(
                reason="handoff_prompt.empty",
                field="handoff_prompt",
                received=body.handoff_prompt,
                expected="non-empty string",
                examples=["Continue from the phase-2 gate review."],
                hint="Supply a non-empty handoff_prompt.",
                detail="handoff_prompt must be non-empty after stripping.",
            ),
        )

    conn = cortex_conn()
    try:
        journal = require_closed_journal_row(conn, session_id)
        conn.execute(
            "UPDATE session_journals SET handoff_prompt = ? WHERE id = ?",
            (handoff_prompt, journal["id"]),
        )
        mirrored = mirror_handoff_to_transcript_entity(conn, session_id, handoff_prompt)
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.error(
            "session_handoff upsert failed for %s",
            session_id,
            exc_info=True,
        )
        raise
    finally:
        conn.close()

    transcript_entity_id = f"transcript:{session_id}" if mirrored else None
    logger.info(
        "session_handoff: upserted session_id=%s journal_row=%d mirrored=%s",
        session_id,
        journal["id"],
        mirrored,
    )
    record(
        "mcp.session.handoff.upsert",
        session_id=session_id,
        journal_row_id=journal["id"],
        mirrored=mirrored,
    )
    return SessionHandoffUpsertResponse(
        session_id=session_id,
        handoff_prompt=handoff_prompt,
        transcript_entity_id=transcript_entity_id,
        journal_row_id=journal["id"],
    )


def _upsert_session_handoff_impl(payload: dict[str, object]) -> dict[str, object]:
    body = SessionHandoffUpsertRequest.model_validate(payload)
    session_id = str(payload["session_id"])
    data = upsert_session_handoff(session_id, body)
    return data.model_dump(mode="json")
