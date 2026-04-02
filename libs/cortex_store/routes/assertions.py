from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Response, status

from ..assertion_quality import validate_assertion
from ..claim_hash import compute_claim_hash
from ..db import cortex_conn, decode_row, json_encode, query
from ..models import (
    AssertionCreate,
    AssertionCreateResponse,
    AssertionItem,
    AssertionList,
    AssertionUpdate,
    NearDuplicateWarning,
    SupersedeRequest,
    SupersedeResponse,
)
from ..near_dup import check_near_duplicate, record_near_duplicate

logger = logging.getLogger("cortex-api.assertions")
router = APIRouter(prefix="/assertions", tags=["assertions"])

_JSON_FIELDS = frozenset({"evidence_uris"})

_VALID_CONFIDENCE = {"confirmed", "believed", "suspected", "hypothesized"}

_ASSERTION_COLS = (
    "id, entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by, "
    "derivation_type, chunk_id, reasoning_summary, is_atomic, is_decontextualized, "
    "observed_at, valid_from, valid_until, superseded_by, "
    "review_status, reviewer, reviewed_at, review_notes, "
    "resolution_status, fulfillment_assertion_id, quality_score, created_at"
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


@router.post("", response_model=AssertionCreateResponse)
def create_assertion(
    body: AssertionCreate, response: Response
) -> AssertionCreateResponse:
    """Create an assertion with quality validation and idempotent dedup.

    v2.4 enforcement: hard rejects return 422 with specific diagnostics.
    Warnings route the assertion to staging (review_status='staged').
    Quality score is computed and stored on every new assertion.
    """
    if body.confidence not in _VALID_CONFIDENCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid confidence: {body.confidence!r}. Must be one of {sorted(_VALID_CONFIDENCE)}",
        )

    validation = validate_assertion(body)

    if validation.rejected:
        diagnostics = [
            {"field": d.field, "message": d.message} for d in validation.hard_reject
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "assertion_quality_rejected",
                "quality_score": validation.quality_score,
                "diagnostics": diagnostics,
            },
        )

    review_status: str | None = None
    validation_warnings: list[dict[str, str]] | None = None
    if validation.route_to_staging:
        review_status = "staged"
        validation_warnings = [
            {"field": d.field, "message": d.message} for d in validation.warnings
        ]
        logger.info(
            "Assertion routed to staging (quality_score=%.2f): %s",
            validation.quality_score,
            body.entity_id,
        )

    claim_hash = compute_claim_hash(body.entity_id, body.claim)

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
            "INSERT OR IGNORE INTO assertions ("
            "  entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by,"
            "  chunk_id, derivation_type, reasoning_summary, observed_at,"
            "  valid_from, valid_until, is_atomic, is_decontextualized, claim_hash,"
            "  resolution_status, fulfillment_assertion_id, quality_score, review_status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                claim_hash,
                body.resolution_status,
                body.fulfillment_assertion_id,
                validation.quality_score,
                review_status,
            ),
        )
        conn.commit()

        was_new = cur.rowcount > 0
        new_id = cur.lastrowid

        if was_new:
            rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
                (new_id,),
            )
        else:
            rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions "
                "WHERE entity_id = ? AND claim_hash = ? AND superseded_by IS NULL",
                (body.entity_id, claim_hash),
            )

        near_dup_warning: NearDuplicateWarning | None = None
        if was_new:
            match = check_near_duplicate(conn, body.entity_id, body.claim, new_id)
            if match:
                record_near_duplicate(conn, new_id, match.existing_id, match.score)
                near_dup_warning = NearDuplicateWarning(
                    existing_id=match.existing_id, score=match.score
                )
    finally:
        conn.close()

    if not rows:
        logger.error(
            "Assertion create: no row found for entity_id=%s claim_hash=%s was_new=%s",
            body.entity_id,
            claim_hash[:16],
            was_new,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assertion created but could not be read back",
        )

    item = AssertionItem(**decode_row(rows[0], _JSON_FIELDS))
    response.status_code = status.HTTP_201_CREATED if was_new else status.HTTP_200_OK
    if not was_new:
        logger.info(
            "Assertion dedup: exact duplicate for entity_id=%s, returning existing id=%d",
            body.entity_id,
            item.id,
        )
    return AssertionCreateResponse(
        was_new=was_new,
        item=item,
        near_duplicate_warning=near_dup_warning,
        validation_warnings=validation_warnings,
    )


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
            "resolution_status": body.resolution_status,
            "fulfillment_assertion_id": body.fulfillment_assertion_id,
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
