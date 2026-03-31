from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from ..db import cortex_conn, decode_row, json_encode, query
from ..models import (
    AssertionCreate,
    AssertionItem,
    AssertionList,
    AssertionUpdate,
    SupersedeRequest,
    SupersedeResponse,
)

logger = logging.getLogger("cortex-api.assertions")
router = APIRouter(prefix="/assertions", tags=["assertions"])

_JSON_FIELDS = frozenset({"evidence_uris"})

_VALID_CONFIDENCE = {"confirmed", "believed", "suspected", "hypothesized"}

_ASSERTION_COLS = (
    "id, entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by, "
    "derivation_type, chunk_id, reasoning_summary, is_atomic, is_decontextualized, "
    "observed_at, valid_from, valid_until, superseded_by, "
    "review_status, reviewer, reviewed_at, review_notes, created_at"
)

_VALID_REVIEW_STATUS = {"committed", "flagged", "staged", "rejected"}


@router.get("", response_model=AssertionList)
def list_assertions(
    entity_id: str | None = None,
    confidence: str | None = None,
    review_status: str | None = None,
    superseded: bool | None = None,
    entity_type: str | None = Query(
        None, description="Filter to assertions on entities of this type"
    ),
    entity_type_exclude: str | None = Query(
        None,
        description="Comma-separated entity types to exclude (e.g. 'legal_matter,person')",
    ),
    valid_at: str | None = Query(
        None, description="World-state: what was true at this date (YYYY-MM-DD)"
    ),
    known_at: str | None = Query(
        None, description="System-state: what the DB knew at this date (YYYY-MM-DD)"
    ),
    limit: int = Query(50, ge=1, le=500),
) -> AssertionList:
    """List assertions with entity, confidence, review_status, superseded, entity type, and temporal filters."""
    clauses: list[str] = []
    params: list[str | int] = []
    needs_join = bool(entity_type or entity_type_exclude)

    if entity_id:
        clauses.append("a.entity_id = ?")
        params.append(entity_id)
    if confidence:
        clauses.append("a.confidence = ?")
        params.append(confidence)
    if review_status:
        clauses.append("a.review_status = ?")
        params.append(review_status)
    if superseded is False:
        clauses.append("a.superseded_by IS NULL")
    elif superseded is True:
        clauses.append("a.superseded_by IS NOT NULL")

    if entity_type:
        clauses.append("e.type = ?")
        params.append(entity_type)
    if entity_type_exclude:
        excluded = [t.strip() for t in entity_type_exclude.split(",") if t.strip()]
        placeholders = ",".join("?" for _ in excluded)
        clauses.append(f"e.type NOT IN ({placeholders})")
        params.extend(excluded)

    if valid_at:
        clauses.append("(a.valid_from IS NULL OR a.valid_from <= ?)")
        params.append(valid_at)
        clauses.append("(a.valid_until IS NULL OR a.valid_until > ?)")
        params.append(valid_at)
        clauses.append("a.superseded_by IS NULL")
    elif known_at:
        clauses.append("a.created_at <= ?")
        params.append(known_at)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    if needs_join:
        cols = ", ".join(f"a.{c.strip()}" for c in _ASSERTION_COLS.split(","))
        sql = (
            f"SELECT {cols} FROM assertions a "
            f"JOIN entities e ON a.entity_id = e.id{where} "
            f"ORDER BY a.created_at DESC LIMIT ?"
        )
    else:
        cols = _ASSERTION_COLS
        sql = f"SELECT {cols} FROM assertions a{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    items: list[AssertionItem] = []
    for row in rows:
        try:
            items.append(AssertionItem(**decode_row(row, _JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s — deserialization failed",
                row.get("id"),
                exc_info=True,
            )
    return AssertionList(items=items)


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
            "  entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by,"
            "  chunk_id, derivation_type, reasoning_summary, observed_at,"
            "  valid_from, valid_until, is_atomic, is_decontextualized"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.entity_id,
                body.claim,
                body.confidence,
                body.confidence_score,
                body.evidence,
                json_encode(body.evidence_uris),
                body.seeded_by,
                body.chunk_id,
                body.derivation_type or "inference",
                body.reasoning_summary,
                body.observed_at,
                body.valid_from,
                body.valid_until,
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


@router.patch("/{assertion_id}", response_model=AssertionItem)
def update_assertion(assertion_id: int, body: AssertionUpdate) -> AssertionItem:
    """Update assertion metadata — supersession, confidence, review status."""
    import datetime as dt

    with cortex_conn() as conn:
        existing = query(
            conn, "SELECT id FROM assertions WHERE id = ?", (assertion_id,)
        )
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Assertion not found: {assertion_id}",
            )

        if body.superseded_by is not None:
            target = query(
                conn, "SELECT id FROM assertions WHERE id = ?", (body.superseded_by,)
            )
            if not target:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Superseding assertion not found: {body.superseded_by}",
                )

        if (
            body.review_status is not None
            and body.review_status not in _VALID_REVIEW_STATUS
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid review_status: {body.review_status!r}. "
                f"Must be one of {sorted(_VALID_REVIEW_STATUS)}",
            )

        if body.confidence is not None and body.confidence not in _VALID_CONFIDENCE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid confidence: {body.confidence!r}. "
                f"Must be one of {sorted(_VALID_CONFIDENCE)}",
            )

        update_map: dict[str, object] = {
            "superseded_by": body.superseded_by,
            "valid_until": body.valid_until,
            "confidence": body.confidence,
            "confidence_score": body.confidence_score,
            "review_status": body.review_status,
            "reviewer": body.reviewer,
            "reviewed_at": body.reviewed_at,
        }
        sets: list[str] = []
        params: list[object] = []
        for col, val in update_map.items():
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)

        if not sets:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No updatable fields provided",
            )

        now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        sets.append("updated_at = ?")
        params.append(now)
        params.append(assertion_id)

        conn.execute(
            f"UPDATE assertions SET {', '.join(sets)} WHERE id = ?", tuple(params)
        )
        conn.commit()

        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )

    return AssertionItem(**decode_row(rows[0], _JSON_FIELDS))


@router.post(
    "/supersede", response_model=SupersedeResponse, status_code=status.HTTP_201_CREATED
)
def supersede_assertion(body: SupersedeRequest) -> SupersedeResponse:
    """Atomic supersession — close old assertion and create replacement in one transaction."""
    import datetime as dt

    if body.confidence not in _VALID_CONFIDENCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid confidence: {body.confidence!r}. Must be one of {sorted(_VALID_CONFIDENCE)}",
        )

    now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = cortex_conn()
    try:
        old_rows = query(
            conn, "SELECT id FROM assertions WHERE id = ?", (body.old_assertion_id,)
        )
        if not old_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Old assertion not found: {body.old_assertion_id}",
            )

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
            "  derivation_type, observed_at, valid_from"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.entity_id,
                body.claim,
                body.confidence,
                body.evidence,
                json_encode(body.evidence_uris),
                body.derivation_type or "inference",
                now,
                body.valid_from,
            ),
        )
        new_id = cur.lastrowid

        conn.execute(
            "UPDATE assertions SET valid_until = ?, superseded_by = ?, updated_at = ? "
            "WHERE id = ?",
            (now, new_id, now, body.old_assertion_id),
        )
        conn.execute(
            "INSERT INTO session_edges ("
            "  session_id, agent, from_node, to_node, edge_type, strength, edge_source, context"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.session_id,
                body.agent,
                f"assertion:{new_id}",
                f"assertion:{body.old_assertion_id}",
                "supersedes",
                1.0,
                "derived",
                "auto-created by supersede tool",
            ),
        )
        conn.commit()

        old_result = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (body.old_assertion_id,),
        )
        new_result = query(
            conn, f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?", (new_id,)
        )
    finally:
        conn.close()

    if not old_result or not new_result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supersession committed but could not read back results",
        )

    return SupersedeResponse(
        old=AssertionItem(**decode_row(old_result[0], _JSON_FIELDS)),
        new=AssertionItem(**decode_row(new_result[0], _JSON_FIELDS)),
    )
