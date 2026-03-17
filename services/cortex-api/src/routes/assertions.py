from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.db import cortex_conn, decode_row, json_encode, query
from src.models import AssertionCreate, AssertionItem, AssertionList

logger = logging.getLogger("cortex-api.assertions")
router = APIRouter(prefix="/assertions", tags=["assertions"])

_JSON_FIELDS = frozenset({"evidence_uris"})

_VALID_CONFIDENCE = {"confirmed", "believed", "suspected", "hypothesized"}


@router.get("", response_model=AssertionList)
def list_assertions(
    entity_id: str | None = None,
    confidence: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> AssertionList:
    """List assertions with optional entity/confidence filters and deterministic ordering."""
    clauses: list[str] = []
    params: list[str | int] = []

    if entity_id:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if confidence:
        clauses.append("confidence = ?")
        params.append(confidence)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT id, entity_id, claim, confidence, evidence, "
        f"evidence_uris, created_at FROM assertions{where} "
        "ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)

    conn = cortex_conn()
    try:
        rows = query(conn, sql, tuple(params))
    finally:
        conn.close()

    return AssertionList(
        items=[AssertionItem(**decode_row(row, _JSON_FIELDS)) for row in rows]
    )


@router.post("", response_model=AssertionItem, status_code=status.HTTP_201_CREATED)
def create_assertion(body: AssertionCreate) -> AssertionItem:
    """Create an assertion for an existing entity and return the persisted row."""
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
            "INSERT INTO assertions (entity_id, claim, confidence, evidence, evidence_uris) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                body.entity_id,
                body.claim,
                body.confidence,
                body.evidence,
                json_encode(body.evidence_uris),
            ),
        )
        conn.commit()
        rows = query(
            conn,
            "SELECT id, entity_id, claim, confidence, evidence, evidence_uris, created_at "
            "FROM assertions WHERE id = ?",
            (cur.lastrowid,),
        )
    finally:
        conn.close()

    if not rows:
        logger.error(
            "Assertion create succeeded but no row returned for entity_id=%s",
            body.entity_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assertion created but could not be read back",
        )
    return AssertionItem(**decode_row(rows[0], _JSON_FIELDS))
