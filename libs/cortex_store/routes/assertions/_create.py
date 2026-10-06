"""POST /assertions — create with quality validation, dedup, contradiction
guard, C2 supersession, near-duplicate detection, auditor-validatability
checks, and background embedding/predicate-extract dispatch.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Response, status
from openapi_mcp.binding import x_mcp
from pydantic import ValidationError

from ...assertion_quality import (
    DERIVATION_TYPE_TAXONOMY,
    check_chunk_locality,
    check_claim_brevity,
    check_confirmed_validatability,
    check_derived_extract_primary,
    validate_assertion,
)
from ...belief_guard import (
    CandidateRecall,
    clear_staged_candidate_recall,
    guard_assertion_write,
    stage_candidate_recall,
)
from ...claim_hash import compute_claim_hash
from ...db import WRITE_LOCK, cortex_conn, decode_row, json_encode, query
from ...enrichment import reindex_assertion_fts
from ...enrichment_dispatch import dispatch_assertion_enrichment_background
from ...entrenchment import compute_entrenchment
from ...events_imprint import graph_recorder_already_known
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
from ...recorder_known_state import (
    check_recorder_known_state,
    should_apply_recorder_known_state,
)
from ...status_trait_write import materialize_graduated_lifecycle
from ...substantiation_sync import recompute_entity_substantiation_status
from ...transcript_evidence_validate import (
    http_detail_from_transcript_error,
    validate_transcript_evidence_uris,
)
from ...transcript_turn_resolve import TranscriptResolveError
from ._shared import (
    _ASSERTION_COLS,
    _JSON_FIELDS,
    _VALID_CONFIDENCE,
    _build_predicate_form_normalize,
    _embed_assertion_background,
    _flag_predicate_normalize_review,
    _normalize_predicate_form_for_write,
    _payload_validation_exception,
    logger,
    router,
)


def _write_assertion_locked(
    conn: Any,
    body: AssertionCreate,
    *,
    claim_hash: str,
    quality_score: float,
    review_status: str | None,
    contradiction_warnings_out: list[ContradictionConflict] | None,
    entrenchment: float,
    predicate_form_to_store: str | None,
    normalize_result: dict | None,
    raw_pf: object,
    norm_dec: object,
    cand_fp: object,
    norm_ver: object,
    commit: bool,
) -> tuple[bool, int, NearDuplicateWarning | None]:
    """Insert one assertion on *conn*. Caller holds ``WRITE_LOCK``.

    ``commit=False`` leaves the transaction open so the caller can add
    another write (deadline outcome) before committing. ``INSERT OR IGNORE``
    plus the active-row select keeps a duplicate claim on the existing id.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO assertions ("
        "  entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by,"
        "  chunk_id, chunk_id_schema, derivation_type, reasoning_summary, observed_at,"
        "  valid_from, valid_until, is_atomic, is_decontextualized, claim_hash,"
        "  resolution_status, fulfillment_assertion_id, quality_score, review_status,"
        "  prospective_summary, events_json, artifact_uri, artifact_storage,"
        "  entrenchment_score, predicate_form, "
        "raw_predicate_form, normalization_decision, candidate_set_fingerprint, normalizer_version,"
        "attributes"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            body.entity_id,
            body.claim,
            body.confidence,
            body.confidence_score,
            body.evidence,
            json_encode(body.evidence_uris),
            body.seeded_by,
            body.chunk_id,
            body.chunk_id_schema,
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
            quality_score,
            review_status,
            body.prospective_summary,
            body.events_json,
            body.artifact_uri,
            body.artifact_storage,
            entrenchment,
            predicate_form_to_store,
            raw_pf,
            norm_dec,
            cand_fp,
            norm_ver,
            json_encode(body.attributes),
        ),
    )

    was_new = cur.rowcount > 0
    new_id = int(cur.lastrowid or 0)
    near_dup_warning: NearDuplicateWarning | None = None

    if was_new:
        if body.force and body.supersedes_id:
            now_str = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            sup_cur = conn.execute(
                "UPDATE assertions SET superseded_by = ?, valid_until = ?, "
                "updated_at = ? WHERE id = ? AND superseded_by IS NULL",
                (new_id, now_str, now_str, body.supersedes_id),
            )
            if sup_cur.rowcount == 0:
                conn.rollback()
                existing = query(
                    conn,
                    "SELECT superseded_by FROM assertions WHERE id = ?",
                    (body.supersedes_id,),
                )
                if not existing:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=(
                            f"supersedes_id assertion {body.supersedes_id} "
                            f"no longer exists (deleted concurrently)"
                        ),
                    )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Assertion {body.supersedes_id} is already "
                        f"superseded by {existing[0].get('superseded_by')}; "
                        f"call POST /assertions/supersede with force=true "
                        f"to override an existing supersedence chain"
                    ),
                )

        if contradiction_warnings_out:
            c2_notes = "; ".join(
                f"Semantic contradiction: #{c.assertion_id} "
                f"(sim={c.similarity:.2f}, "
                f"source={c.retrieval_source or 'cosine'})"
                for c in contradiction_warnings_out
            )
            conn.execute(
                "UPDATE assertions SET review_notes = ? WHERE id = ?",
                (c2_notes, new_id),
            )

        match = check_near_duplicate(conn, body.entity_id, body.claim, new_id)
        if match:
            record_near_duplicate(
                conn, new_id, match.existing_id, match.score, commit=commit
            )
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

        if normalize_result and normalize_result.get("requires_human_review"):
            _flag_predicate_normalize_review(conn, new_id, normalize_result)

        recompute_entity_substantiation_status(conn, body.entity_id)
        if review_status != "staged":
            materialize_graduated_lifecycle(conn, body.entity_id)
    else:
        existing_rows = query(
            conn,
            "SELECT id FROM assertions "
            "WHERE entity_id = ? AND claim_hash = ? AND superseded_by IS NULL",
            (body.entity_id, claim_hash),
        )
        if existing_rows:
            new_id = int(existing_rows[0]["id"])

    if commit:
        conn.commit()
    return was_new, new_id, near_dup_warning


def start_new_assertion_side_effects(
    *,
    item_id: int,
    claim: str,
    entity_id: str,
    confidence: str,
    derivation_type: str,
    entrenchment_score: float,
    observed_at: str | None,
    prospective_summary: str | None,
    events_json: object,
) -> None:
    """FTS, enrichment, predicate extract, and embedding after a committed insert."""
    threading.Thread(
        target=reindex_assertion_fts, args=(item_id,), daemon=True
    ).start()
    dispatch_assertion_enrichment_background(
        item_id, claim, entity_id, confidence
    )
    dispatch_predicate_extract_background(item_id, claim, entity_id)
    _embed_assertion_background(
        item_id,
        {
            "claim": claim,
            "entity_id": entity_id,
            "confidence": confidence,
            "derivation_type": derivation_type,
            "entrenchment_score": entrenchment_score,
            "observed_at": observed_at,
            "prospective_summary": prospective_summary,
            "events_json": events_json,
        },
    )


@dataclass
class PreparedAssertionWrite:
    """Guard and scoring results computed with no writer lock held."""

    claim_hash: str
    quality_score: float
    review_status: str | None
    validation_warnings: list[dict[str, str]] | None
    contradiction_warnings_out: list[ContradictionConflict] | None
    entrenchment: float
    predicate_form_to_store: str | None
    normalize_result: dict | None
    raw_pf: object
    norm_dec: object
    cand_fp: object
    norm_ver: object
    early: AssertionCreateResponse | None = None


def prepare_assertion_write(
    body: AssertionCreate, response: Response
) -> PreparedAssertionWrite:
    """Validation, belief guard, entrenchment, and predicate normalize.

    Uses its own connection and does not acquire ``WRITE_LOCK``. The guard's
    embedding call stays off the global writer lock.
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

    try:
        validate_transcript_evidence_uris(body.evidence_uris)
    except TranscriptResolveError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=http_detail_from_transcript_error(exc),
        ) from exc

    review_status: str | None = None
    validation_warnings: list[dict[str, str]] | None = None
    if validation.route_to_staging:
        review_status = "staged"
        validation_warnings = [
            {"field": d.field, "category": d.category, "message": d.message}
            for d in validation.warnings
        ]

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

    brevity_warnings = check_claim_brevity(
        claim=body.claim,
        evidence_uris=body.evidence_uris,
        entity_id=body.entity_id,
        acknowledge_audit_gaps=body.acknowledge_audit_gaps,
    )
    if brevity_warnings:
        if validation_warnings is None:
            validation_warnings = []
        validation_warnings.extend(brevity_warnings)

    provenance_warnings = check_derived_extract_primary(
        body.evidence_uris
    ) + check_chunk_locality(
        derivation_type=body.derivation_type,
        claim=body.claim,
        evidence_uris=body.evidence_uris,
        chunk_id=body.chunk_id,
    )
    if provenance_warnings:
        if validation_warnings is None:
            validation_warnings = []
        validation_warnings.extend(provenance_warnings)

    if body.supersedes_id is not None and not body.force:
        if validation_warnings is None:
            validation_warnings = []
        validation_warnings.append(
            {
                "field": "supersedes_id",
                "category": "protocol",
                "message": (
                    f"supersedes_id={body.supersedes_id} has no effect without "
                    "force=true: the target's superseded_by is NOT set and this "
                    "assertion is created as a sibling. Use the `supersede` op "
                    "for atomic close+create, or pass force=true to chain lineage."
                ),
            }
        )

    if body.dry_run:
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
        finally:
            conn.close()
        return PreparedAssertionWrite(
            claim_hash="",
            quality_score=prepared.quality_score,
            review_status=review_status,
            validation_warnings=validation_warnings,
            contradiction_warnings_out=None,
            entrenchment=0.0,
            predicate_form_to_store=None,
            normalize_result=None,
            raw_pf=None,
            norm_dec=None,
            cand_fp=None,
            norm_ver=None,
            early=AssertionCreateResponse(
                was_new=False,
                item=None,
                dry_run=True,
                would_write=True,
                validation_warnings=validation_warnings,
            ),
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
        entity_type_row = query(
            conn, "SELECT type FROM entities WHERE id = ?", (body.entity_id,)
        )
        entity_type = str(entity_type_row[0]["type"]) if entity_type_row else None
        contradiction_warnings_out: list[ContradictionConflict] | None = None
        if body.force and body.supersedes_id is not None:
            sup_target = query(
                conn, "SELECT id FROM assertions WHERE id = ?", (body.supersedes_id,)
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
                    retrieval_source=c.retrieval_source,
                )
                for c in guard.contradiction_warnings
            ]
        if should_apply_recorder_known_state(body, entity_type=entity_type):
            known = check_recorder_known_state(conn, body)
            if known.already_known and known.matched_assertion_id is not None:
                rows = query(
                    conn,
                    f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
                    (known.matched_assertion_id,),
                )
                if rows:
                    item = AssertionItem(**decode_row(rows[0], _JSON_FIELDS))
                    graph_recorder_already_known(
                        entity_id=body.entity_id,
                        matched_assertion_id=known.matched_assertion_id,
                        reason=known.known_state_reason or "already_known",
                        anchor=known.anchor,
                    )
                    response.status_code = status.HTTP_200_OK
                    return PreparedAssertionWrite(
                        claim_hash=claim_hash,
                        quality_score=prepared.quality_score,
                        review_status=review_status,
                        validation_warnings=validation_warnings,
                        contradiction_warnings_out=contradiction_warnings_out,
                        entrenchment=0.0,
                        predicate_form_to_store=None,
                        normalize_result=None,
                        raw_pf=None,
                        norm_dec=None,
                        cand_fp=None,
                        norm_ver=None,
                        early=AssertionCreateResponse(
                            was_new=False,
                            item=item,
                            validation_warnings=validation_warnings,
                            contradiction_warnings=contradiction_warnings_out,
                            already_known=True,
                            known_state_reason=known.known_state_reason,
                            matched_assertion_id=known.matched_assertion_id,
                        ),
                    )
        entrenchment = compute_entrenchment(
            confidence=body.confidence,
            derivation_type=body.derivation_type or "inference",
            observed_at=body.observed_at,
            created_at=None,
            entity_id=body.entity_id,
            conn=conn,
        )
        predicate_form_to_store: str | None = None
        normalize_result: dict | None = None
        if body.predicate_form is not None:
            predicate_form_to_store, normalize_result = (
                _normalize_predicate_form_for_write(
                    body.entity_id, body.predicate_form, body.claim, conn
                )
            )
        raw_pf = (
            normalize_result.get("raw_predicate_form") if normalize_result else None
        )
        norm_dec = (
            normalize_result.get("normalization_decision") if normalize_result else None
        )
        cand_fp = (
            normalize_result.get("candidate_set_fingerprint")
            if normalize_result
            else None
        )
        norm_ver = (
            normalize_result.get("normalizer_version") if normalize_result else None
        )
        return PreparedAssertionWrite(
            claim_hash=claim_hash,
            quality_score=validation.quality_score,
            review_status=review_status,
            validation_warnings=validation_warnings,
            contradiction_warnings_out=contradiction_warnings_out,
            entrenchment=entrenchment,
            predicate_form_to_store=predicate_form_to_store,
            normalize_result=normalize_result,
            raw_pf=raw_pf,
            norm_dec=norm_dec,
            cand_fp=cand_fp,
            norm_ver=norm_ver,
        )
    finally:
        conn.close()


@router.post("", response_model=AssertionCreateResponse, openapi_extra=x_mcp("assert"))
def create_assertion(
    body: AssertionCreate,
    response: Response,
    *,
    conn: Any | None = None,
    commit: bool = True,
    hold_lock: bool = True,
    side_effect_out: dict[str, object] | None = None,
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
    prepared = prepare_assertion_write(body, response)
    if prepared.early is not None:
        return prepared.early
    claim_hash = prepared.claim_hash
    review_status = prepared.review_status
    validation_warnings = prepared.validation_warnings
    contradiction_warnings_out = prepared.contradiction_warnings_out
    entrenchment = prepared.entrenchment
    predicate_form_to_store = prepared.predicate_form_to_store
    normalize_result = prepared.normalize_result
    raw_pf = prepared.raw_pf
    norm_dec = prepared.norm_dec
    cand_fp = prepared.cand_fp
    norm_ver = prepared.norm_ver

    owns_conn = conn is None
    if owns_conn:
        conn = cortex_conn()
    assert conn is not None
    try:
        near_dup_warning: NearDuplicateWarning | None = None

        def _locked_write() -> tuple[bool, int, NearDuplicateWarning | None]:
            return _write_assertion_locked(
                conn,
                body,
                claim_hash=claim_hash,
                quality_score=prepared.quality_score,
                review_status=review_status,
                contradiction_warnings_out=contradiction_warnings_out,
                entrenchment=entrenchment,
                predicate_form_to_store=predicate_form_to_store,
                normalize_result=normalize_result,
                raw_pf=raw_pf,
                norm_dec=norm_dec,
                cand_fp=cand_fp,
                norm_ver=norm_ver,
                commit=commit,
            )

        if hold_lock:
            with WRITE_LOCK:
                was_new, new_id, near_dup_warning = _locked_write()
        else:
            was_new, new_id, near_dup_warning = _locked_write()

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
        if owns_conn:
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
    already_known = False
    known_state_reason: str | None = None
    matched_assertion_id: int | None = None
    if not was_new:
        logger.info(
            "Assertion dedup: exact duplicate for entity_id=%s, returning existing id=%d",
            body.entity_id,
            item.id,
        )
        already_known = True
        known_state_reason = "exact_claim_hash"
        matched_assertion_id = item.id
        graph_recorder_already_known(
            entity_id=body.entity_id,
            matched_assertion_id=item.id,
            reason="exact_claim_hash",
        )
    elif commit:
        start_new_assertion_side_effects(
            item_id=item.id,
            claim=body.claim,
            entity_id=body.entity_id,
            confidence=body.confidence,
            derivation_type=body.derivation_type or "inference",
            entrenchment_score=entrenchment,
            observed_at=body.observed_at,
            prospective_summary=body.prospective_summary,
            events_json=body.events_json,
        )
    elif side_effect_out is not None:
        side_effect_out.update(
            item_id=item.id,
            claim=body.claim,
            entity_id=body.entity_id,
            confidence=body.confidence,
            derivation_type=body.derivation_type or "inference",
            entrenchment_score=entrenchment,
            observed_at=body.observed_at,
            prospective_summary=body.prospective_summary,
            events_json=body.events_json,
        )

    predicate_form_normalize_out = None
    if normalize_result is not None and body.predicate_form is not None:
        predicate_form_normalize_out = _build_predicate_form_normalize(
            body.predicate_form, normalize_result
        )

    return AssertionCreateResponse(
        was_new=was_new,
        item=item,
        near_duplicate_warning=near_dup_warning,
        validation_warnings=validation_warnings,
        contradiction_warnings=contradiction_warnings_out,
        predicate_form_normalize=predicate_form_normalize_out,
        already_known=already_known,
        known_state_reason=known_state_reason,
        matched_assertion_id=matched_assertion_id,
    )


def _create_assertion_impl(
    payload: dict[str, object],
    *,
    candidate_recall: CandidateRecall | None = None,
) -> dict[str, object]:
    """Create one assertion. A staged recall is the nudge's hybrid search.

    HTTP callers omit ``candidate_recall`` and the guard searches once.
    MCP assert passes the nudge recall so the guard does not search again.
    The park is cleared on the way out, including when create raises before
    the guard consumes it.
    """
    response = Response()
    try:
        body = AssertionCreate.model_validate(payload)
    except ValidationError as exc:
        raise _payload_validation_exception(exc) from exc
    staged_here = False
    if candidate_recall is not None:
        stage_candidate_recall(body.entity_id, body.claim, candidate_recall)
        staged_here = True
    try:
        result = create_assertion(body, response)
    finally:
        if staged_here:
            clear_staged_candidate_recall()
    return result.model_dump(mode="json")


__all__ = ["_create_assertion_impl", "create_assertion"]
