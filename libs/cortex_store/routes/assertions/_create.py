"""POST /assertions — create with quality validation, dedup, contradiction
guard, C2 supersession, near-duplicate detection, auditor-validatability
checks, and background embedding/predicate-extract dispatch.
"""

from __future__ import annotations

import threading

from fastapi import HTTPException, Response, status
from pydantic import ValidationError

from ...assertion_quality import (
    DERIVATION_TYPE_TAXONOMY,
    check_confirmed_validatability,
    validate_assertion,
)
from ...belief_guard import guard_assertion_write
from ...claim_hash import compute_claim_hash
from ...db import WRITE_LOCK, cortex_conn, decode_row, json_encode, query
from ...enrichment import enrich_background, reindex_assertion_fts
from ...entrenchment import compute_entrenchment
from ...graph_utils import check_contradictions
from ...models import (
    AssertionCreate,
    AssertionCreateResponse,
    AssertionItem,
    ContradictionConflict,
    NearDuplicateWarning,
)
from ...near_dup import check_near_duplicate, record_near_duplicate
from ...predicate_extract_dispatch import dispatch_predicate_extract_background
from ._shared import (
    _ASSERTION_COLS,
    _JSON_FIELDS,
    _VALID_CONFIDENCE,
    _embed_assertion_background,
    _payload_validation_exception,
    logger,
    router,
)


@router.post("", response_model=AssertionCreateResponse)
def create_assertion(
    body: AssertionCreate, response: Response
) -> AssertionCreateResponse:
    """Create an assertion with quality validation and idempotent dedup.

    v2.4 enforcement: hard rejects return 422 with specific diagnostics.
    Warnings route the assertion to staging (review_status='staged').
    Quality score is computed and stored on every new assertion.

    Auditor-validatability (Checks 1–3): when confidence='confirmed', advisory
    warnings are appended to validation_warnings if evidence_uris is absent,
    derivation_type is inference, or the claim lacks an embedded verbatim quote
    for verbatim-expected derivation types. These do NOT block the write.
    Pass acknowledge_audit_gaps=['no_evidence_uris'|'inference_confirmed'|'no_verbatim']
    to suppress individual checks with documented intent.
    See agent_skill:auditor-validatable-confidence.
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
                "valid_derivation_types": DERIVATION_TYPE_TAXONOMY,
            },
        )

    review_status: str | None = None
    validation_warnings: list[dict[str, str]] | None = None
    if validation.route_to_staging:
        review_status = "staged"
        validation_warnings = [
            {"field": d.field, "category": d.category, "message": d.message}
            for d in validation.warnings
        ]
        logger.info(
            "Assertion routed to staging (quality_score=%.2f): %s",
            validation.quality_score,
            body.entity_id,
        )

    auditor_warnings = check_confirmed_validatability(
        confidence=body.confidence,
        evidence_uris=body.evidence_uris,
        derivation_type=body.derivation_type,
        claim=body.claim,
        acknowledge_audit_gaps=body.acknowledge_audit_gaps,
    )
    if auditor_warnings:
        if validation_warnings is None:
            validation_warnings = []
        validation_warnings.extend(auditor_warnings)

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

        # C2: Write-path contradiction check (entity-local, AGM G3)
        contradiction_warnings_out: list[ContradictionConflict] | None = None
        if body.force and body.supersedes_id is not None:
            sup_target = query(
                conn,
                "SELECT id FROM assertions WHERE id = ?",
                (body.supersedes_id,),
            )
            if not sup_target:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(f"supersedes_id assertion not found: {body.supersedes_id}"),
                )

        guard = guard_assertion_write(
            conn, body.entity_id, body.claim, force=body.force
        )
        if not guard.allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=guard.block_detail,
            )
        if guard.review_status:
            review_status = guard.review_status
        if guard.contradiction_warnings:
            contradiction_warnings_out = [
                ContradictionConflict(
                    assertion_id=c.assertion_id,
                    claim=c.claim,
                    confidence=c.confidence,
                    similarity=c.similarity,
                )
                for c in guard.contradiction_warnings
            ]

        entrenchment = compute_entrenchment(
            confidence=body.confidence,
            derivation_type=body.derivation_type or "inference",
            observed_at=body.observed_at,
            created_at=None,
            entity_id=body.entity_id,
            conn=conn,
        )

        near_dup_warning: NearDuplicateWarning | None = None

        with WRITE_LOCK:
            cur = conn.execute(
                "INSERT OR IGNORE INTO assertions ("
                "  entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by,"
                "  chunk_id, derivation_type, reasoning_summary, observed_at,"
                "  valid_from, valid_until, is_atomic, is_decontextualized, claim_hash,"
                "  resolution_status, fulfillment_assertion_id, quality_score, review_status,"
                "  prospective_summary, events_json, artifact_uri, artifact_storage,"
                "  entrenchment_score"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    body.prospective_summary,
                    body.events_json,
                    body.artifact_uri,
                    body.artifact_storage,
                    entrenchment,
                ),
            )

            was_new = cur.rowcount > 0
            new_id = cur.lastrowid

            if was_new:
                if body.force and body.supersedes_id:
                    import datetime as dt

                    now_str = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    conn.execute(
                        "UPDATE assertions SET superseded_by = ?, valid_until = ?, "
                        "updated_at = ? WHERE id = ? AND superseded_by IS NULL",
                        (new_id, now_str, now_str, body.supersedes_id),
                    )

                if contradiction_warnings_out:
                    c2_notes = "; ".join(
                        f"Semantic contradiction: #{c.assertion_id} "
                        f"(sim={c.similarity:.2f})"
                        for c in contradiction_warnings_out
                    )
                    conn.execute(
                        "UPDATE assertions SET review_notes = ? WHERE id = ?",
                        (c2_notes, new_id),
                    )

                match = check_near_duplicate(conn, body.entity_id, body.claim, new_id)
                if match:
                    record_near_duplicate(conn, new_id, match.existing_id, match.score)
                    near_dup_warning = NearDuplicateWarning(
                        existing_id=match.existing_id, score=match.score
                    )

                contradiction = check_contradictions(conn, body.entity_id, body.claim)
                if contradiction.flagged:
                    conn.execute(
                        "UPDATE assertions SET review_status = ?, "
                        "review_notes = CASE WHEN review_notes IS NOT NULL "
                        "THEN review_notes || '; ' || ? ELSE ? END "
                        "WHERE id = ?",
                        (
                            "flagged",
                            contradiction.review_notes,
                            contradiction.review_notes,
                            new_id,
                        ),
                    )
                    logger.info(
                        "Assertion %d flagged: contradiction with %s via edge #%s",
                        new_id,
                        contradiction.contradicting_entity,
                        contradiction.edge_id,
                    )

            conn.commit()

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
    else:
        threading.Thread(
            target=reindex_assertion_fts, args=(item.id,), daemon=True
        ).start()
        enrich_background(item.id, body.claim, body.entity_id, body.confidence)
        dispatch_predicate_extract_background(item.id, body.claim, body.entity_id)
        _embed_assertion_background(
            item.id,
            {
                "claim": body.claim,
                "entity_id": body.entity_id,
                "confidence": body.confidence,
                "derivation_type": body.derivation_type or "inference",
                "entrenchment_score": entrenchment,
                "observed_at": body.observed_at,
                "prospective_summary": body.prospective_summary,
                "events_json": body.events_json,
            },
        )

    return AssertionCreateResponse(
        was_new=was_new,
        item=item,
        near_duplicate_warning=near_dup_warning,
        validation_warnings=validation_warnings,
        contradiction_warnings=contradiction_warnings_out,
    )


def _create_assertion_impl(payload: dict[str, object]) -> dict[str, object]:
    response = Response()
    try:
        body = AssertionCreate.model_validate(payload)
    except ValidationError as exc:
        raise _payload_validation_exception(exc) from exc
    result = create_assertion(body, response)
    return result.model_dump(mode="json")


__all__ = ["_create_assertion_impl", "create_assertion"]
