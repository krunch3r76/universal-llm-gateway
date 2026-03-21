from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.db import cortex_conn, decode_row, json_encode, query
from src.models import AssertionCreate, AssertionItem, AssertionList

logger = logging.getLogger("cortex-api.assertions")
router = APIRouter(prefix="/assertions", tags=["assertions"])

_JSON_FIELDS = frozenset({"evidence_uris"})

_VALID_CONFIDENCE = {"confirmed", "believed", "suspected", "hypothesized"}

_ASSERTION_COLS = (
    "id, entity_id, claim, confidence, evidence, evidence_uris, "
    "chunk_id, derivation_type, reasoning_summary, observed_at, "
    "valid_from, valid_until, validity_precision, confidence_score, "
    "temporal_type, is_atomic, is_decontextualized, human_reviewed, "
    "superseded_by, review_notes, created_at"
)


@router.get("", response_model=AssertionList)
def list_assertions(
    entity_id: str | None = None,
    confidence: str | None = None,
    superseded: bool | None = None,
    valid_at: str | None = Query(
        None, description="World-state: what was true at this date (YYYY-MM-DD)"
    ),
    known_at: str | None = Query(
        None, description="System-state: what the DB knew at this date (YYYY-MM-DD)"
    ),
    limit: int = Query(50, ge=1, le=500),
) -> AssertionList:
    """List assertions with temporal, entity, confidence, and superseded filters.

    Args:
        entity_id: Filter assertions by a specific entity ID.
        confidence: Filter assertions by a specific confidence level.
        superseded: Filter for superseded (True) or non-superseded (False) assertions.
        valid_at: World-state: what was true at this date (YYYY-MM-DD).
        known_at: System-state: what the DB knew at this date (YYYY-MM-DD).
        limit: Maximum number of assertions to return.

    Temporal query semantics (mutually exclusive):
    - ``valid_at``: world-state — what was true on that date
    - ``known_at``: system-state — what the DB had recorded by that date
    """
    clauses: list[str] = []
    params: list[str | int] = []

    if entity_id:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if confidence:
        clauses.append("confidence = ?")
        params.append(confidence)
    if superseded is False:
        clauses.append("superseded_by IS NULL")
    elif superseded is True:
        clauses.append("superseded_by IS NOT NULL")

    if valid_at:
        clauses.append("(valid_from IS NULL OR valid_from <= ?)")
        params.append(valid_at)
        clauses.append("(valid_until IS NULL OR valid_until > ?)")
        params.append(valid_at)
        clauses.append("superseded_by IS NULL")
    elif known_at:
        clauses.append("created_at <= ?")
        params.append(known_at)
        clauses.append("(superseded_at IS NULL OR superseded_at > ?)")
        params.append(known_at)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {_ASSERTION_COLS} FROM assertions{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    return AssertionList(
        items=[AssertionItem(**decode_row(row, _JSON_FIELDS)) for row in rows]
    )


@router.post("", response_model=AssertionItem, status_code=status.HTTP_201_CREATED)
def create_assertion(body: AssertionCreate) -> AssertionItem:
    """Create an assertion for an existing entity with full v2 provenance."""
    if body.confidence not in _VALID_CONFIDENCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid confidence: {body.confidence!r}. Must be one of {sorted(_VALID_CONFIDENCE)}",
        )

    conn = cortex_conn()
    try:
        entities = query(
            conn, "SELECT id FROM entities WHERE id = ?", (body.entity_id,)
        )
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {body.entity_id}",
            )

        cur = conn.execute(
            "INSERT INTO assertions ("
            "  entity_id, claim, confidence, evidence, evidence_uris,"
            "  chunk_id, derivation_type, reasoning_summary, observed_at,"
            "  valid_from, valid_until, validity_precision, confidence_score,"
            "  temporal_type, is_atomic, is_decontextualized"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.entity_id,
                body.claim,
                body.confidence,
                body.evidence,
                json_encode(body.evidence_uris),
                body.chunk_id,
                body.derivation_type,
                body.reasoning_summary,
                body.observed_at,
                body.valid_from,
                body.valid_until,
                body.validity_precision,
                body.confidence_score,
                body.temporal_type,
                body.is_atomic,
                body.is_decontextualized,
            ),
        )
        conn.commit()
        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (cur.lastrowid,),
        )
    finally:
        conn.close()

    if not rows:
        logger.error(
            "Assertion create succeeded but no row returned for entity_id=%s, assertion_id=%s",
            body.entity_id,
            cur.lastrowid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assertion created but could not be read back",
        )
    return AssertionItem(**decode_row(rows[0], _JSON_FIELDS))
