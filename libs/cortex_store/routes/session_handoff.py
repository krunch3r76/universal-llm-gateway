"""POST /session-journals/{session_id}/handoff — post-close handoff upsert."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from openapi_mcp.binding import x_mcp
from universal_logging import get_logger

from ..db import cortex_conn
from ..dispatch_ops._shared import (
    _FILES_ROOT,
    _SESSION_ID_EXAMPLES,
    _SESSION_ID_RE,
    _SESSION_ID_RE_SOURCE,
    record,
)
from ..models import SessionHandoffUpsertRequest, SessionHandoffUpsertResponse
from ..session_close_validation import build_validation_error
from ..session_handoff import (
    WRITE_PATH_HANDOFF_UPSERT,
    mirror_handoff_to_transcript_entity,
    require_closed_journal_row,
    resolve_handoff_for_write,
)

logger = get_logger("cortex-api.session_handoff")
router = APIRouter(prefix="/session-journals", tags=["session-journals"])


@router.post(
    "/{session_id}/handoff",
    response_model=SessionHandoffUpsertResponse,
    status_code=status.HTTP_200_OK,
    openapi_extra=x_mcp("session_handoff_upsert"),
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
                    "precede the YYYY-MM-DD-HHMMSS-{3hex} timestamp."
                ),
                detail=(
                    f"session_id {session_id!r} does not match pattern "
                    f"{_SESSION_ID_RE_SOURCE}."
                ),
            ),
        )

    if not body.handoff_source_path and not body.handoff_prompt.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_validation_error(
                reason="handoff_prompt.empty",
                field="handoff_prompt",
                received=body.handoff_prompt,
                expected="non-empty handoff_prompt or handoff_source_path",
                examples=["Continue from the phase-2 gate review."],
                hint="Supply handoff_prompt or a marker-backed handoff_source_path.",
                detail="handoff_prompt must be non-empty when no source path is given.",
            ),
        )

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolution = resolve_handoff_for_write(
        files_root=_FILES_ROOT,
        write_path=WRITE_PATH_HANDOFF_UPSERT,
        written_at=now,
        session_id=session_id,
        handoff_source_path=body.handoff_source_path,
        handoff_source_section=body.handoff_source_section,
        handoff_prompt=body.handoff_prompt,
        expected_handoff_prompt=body.expected_handoff_prompt,
        expected_derived_handoff_prompt_sha256=(
            body.expected_derived_handoff_prompt_sha256
        ),
        expected_source_file_sha256=body.expected_source_file_sha256,
    )
    handoff_prompt = resolution.handoff_prompt
    provenance = resolution.provenance
    if not handoff_prompt and not body.handoff_source_path:
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
        mirrored = mirror_handoff_to_transcript_entity(
            conn,
            session_id,
            handoff_prompt,
            provenance,
            handoff_verification=resolution.handoff_verification,
        )
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
