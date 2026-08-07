"""POST /assertions/supersede — atomic close+create with clone-then-override
field carryover, C1 semantic-impact check, auditor-validatability warnings,
and supersession-edge bookkeeping.
"""

from __future__ import annotations

import datetime as dt
import threading

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp
from pydantic import ValidationError

from ... import vector_store
from ...assertion_quality import (
    DERIVATION_TYPE_TAXONOMY,
    ValidationDiagnostic,
    check_chunk_locality,
    check_claim_brevity,
    check_confirmed_validatability,
    check_derived_extract_primary,
    validate_assertion,
)
from ...belief_guard import analyze_assertion_impact
from ...config import supersede_validation_mode
from ...db import WRITE_LOCK, cortex_conn, decode_row, json_encode, query
from ...enrichment import enrich_old_assertion_events, reindex_assertion_fts
from ...enrichment_dispatch import dispatch_assertion_enrichment_background
from ...entrenchment import compute_entrenchment
from ...event_publisher import cortex_supersede_would_reject
from ...models import (
    AssertionCreate,
    AssertionItem,
    SupersedeRequest,
    SupersedeResponse,
)
from ...predicate_extract_dispatch import dispatch_predicate_extract_background
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
    _embed_assertion_background,
    _normalize_predicate_form_for_write,
    _payload_validation_exception,
    logger,
    router,
)


def _hard_reject_rule_ids(diagnostics: list[ValidationDiagnostic]) -> list[str]:
    rule_ids: list[str] = []
    for diag in diagnostics:
        if diag.field == "derivation_type":
            rule_ids.append("R1")
        elif diag.field == "evidence_uris" and "thread_compression" in diag.message:
            rule_ids.append("R2a")
        elif diag.field == "chunk_id" and "thread_compression" in diag.message:
            rule_ids.append("R2b")
        elif diag.field in {"chunk_id", "evidence_uris"}:
            rule_ids.append("R3")
        elif diag.field == "valid_from":
            rule_ids.append("R4")
        elif diag.field == "observed_at":
            rule_ids.append("R5")
        else:
            rule_ids.append(diag.field)
    return rule_ids


def _staging_rule_ids(
    validation_warnings: list[ValidationDiagnostic], quality_score: float
) -> list[str]:
    rule_ids: list[str] = []
    if any(d.field == "reasoning_summary" for d in validation_warnings):
        rule_ids.append("R6")
    if quality_score < 0.7:
        rule_ids.append("R7")
    return rule_ids


@router.post(
    "/supersede",
    response_model=SupersedeResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=x_mcp("supersede"),
)
def supersede_assertion(body: SupersedeRequest) -> SupersedeResponse:
    """Atomic supersession — close old assertion and create replacement in one transaction.

    Fix-path (a)+(b): the new assertion inherits all structured fields from the
    superseded assertion for any field not explicitly present in the caller's
    payload (detected via model_fields_set).  Callers that want to intentionally
    drop a field must pass it as explicit null in their JSON payload.

    Idempotency guard: when the target old_assertion_id's superseded_by is
    already non-null, the call returns 409 Conflict — the new INSERT is
    rolled back; the lineage pointer is preserved. Pass force=true to widen
    the SQL CAS and overwrite a known-existing supersedence chain. See
    decision:cortex-api-write-serialization / assertion 9956 for the
    WRITE_LOCK-vs-SQL-CAS doctrine and friction 9824 for the C1 trigger.

    Auditor-validatability (Checks 1–3): same advisory warnings as create_assertion
    are appended to the response's validation_warnings field when confidence='confirmed'.
    Pass acknowledge_audit_gaps=['no_evidence_uris'|'inference_confirmed'|'no_verbatim']
    to suppress individual checks. See agent_skill:auditor-validatable-confidence.
    """
    if body.confidence not in _VALID_CONFIDENCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid confidence: {body.confidence!r}. Must be one of {sorted(_VALID_CONFIDENCE)}",
        )

    now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = cortex_conn()
    try:
        # Fetch old assertion with full field projection — needed for carryover.
        old_rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (body.old_assertion_id,),
        )
        if not old_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Old assertion not found: {body.old_assertion_id}",
            )

        # Carryover resolution: fields absent from the caller's payload are
        # inherited from the superseded assertion so that a simple claim-rewrite
        # never silently downgrades evidence_uris / derivation_type / valid_from
        # or any other structured field.
        old_data = decode_row(old_rows[0], _JSON_FIELDS)
        specified = body.model_fields_set

        def _resolve(field: str, default: object = None) -> object:
            # ∀ field ∈ specified: use body value (explicit override or explicit null-drop).
            # ∀ field ∉ specified: inherit from predecessor (no-silent-drop guarantee).
            return (
                getattr(body, field)
                if field in specified
                else old_data.get(field, default)
            )

        eff_evidence_uris: list[str] | None = _resolve("evidence_uris")  # type: ignore[assignment]
        _raw_derivation_type = _resolve("derivation_type")
        eff_derivation_type: str = _raw_derivation_type or "inference"  # type: ignore[assignment]
        if not _raw_derivation_type:
            logger.info(
                "supersede_default_to_inference entity_id=%s force=%s derivation_type_supplied=%s",
                body.entity_id,
                body.force,
                "derivation_type" in specified,
            )
        eff_valid_from: str | None = _resolve("valid_from")  # type: ignore[assignment]
        eff_reasoning_summary: str | None = _resolve("reasoning_summary")  # type: ignore[assignment]
        eff_seeded_by: str | None = _resolve("seeded_by")  # type: ignore[assignment]
        eff_chunk_id: str | None = _resolve("chunk_id")  # type: ignore[assignment]
        eff_confidence_score: float | None = _resolve("confidence_score")  # type: ignore[assignment]

        # predicate_form resolution — three branches:
        #  1. Explicit non-null supply  → normalise before INSERT (create path).
        #  2. Claim changed, no explicit supply → DROP the inherited form and
        #     schedule re-derivation. Cloning the predecessor's predicate_form
        #     verbatim when the claim changed encodes the OLD claim's structure
        #     on the new row (e.g. a status claim wearing the predecessor's
        #     has_attribute(...)), producing phantom predicate_summary entries.
        #     Ledger fields stay null until the async re-extract re-normalises
        #     from the new claim — same contract as a fresh create.
        #  3. Claim unchanged, no explicit supply → inherit canonical value
        #     as-is (already normalised at original write time).
        # Explicit null in payload intentionally drops the field on the new row.
        # (2) fixes the supersede-carryover staleness reported on thread 1227;
        # (1)/(3) preserve friction 9826 / todo:cortex-supersede-predicate-form-carryover.
        predicate_form_explicit = "predicate_form" in specified
        claim_changed = body.claim != old_data.get("claim")
        eff_predicate_form: str | None = _resolve("predicate_form")  # type: ignore[assignment]
        normalize_result: dict | None = None
        redrive_predicate_extract = False
        if predicate_form_explicit and body.predicate_form is not None:
            eff_predicate_form, normalize_result = _normalize_predicate_form_for_write(
                body.entity_id, body.predicate_form, body.claim, conn
            )
        elif not predicate_form_explicit and claim_changed:
            eff_predicate_form = None
            redrive_predicate_extract = True

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

        entities = query(
            conn, "SELECT id FROM entities WHERE id = ?", (body.entity_id,)
        )
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {body.entity_id}",
            )

        synthetic = AssertionCreate.model_validate(
            {
                "entity_id": body.entity_id,
                "claim": body.claim,
                "confidence": body.confidence,
                "evidence": body.evidence,
                "derivation_type": eff_derivation_type,
                "chunk_id": eff_chunk_id,
                "evidence_uris": eff_evidence_uris,
                "valid_from": eff_valid_from,
                "observed_at": now,
                "reasoning_summary": eff_reasoning_summary,
                "confidence_score": eff_confidence_score,
            }
        )
        validation = validate_assertion(synthetic)
        quality_validation_warnings: list[dict[str, str]] = []

        try:
            validate_transcript_evidence_uris(eff_evidence_uris)
        except TranscriptResolveError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=http_detail_from_transcript_error(exc),
            ) from exc

        if validation.rejected:
            reject_rule_ids = _hard_reject_rule_ids(validation.hard_reject)
            mode = supersede_validation_mode()
            governing_fields = {
                "R2": ("derivation_type", "evidence_uris", "chunk_id"),
                "R3": ("derivation_type", "chunk_id", "evidence_uris"),
                "R4": ("claim", "valid_from"),
            }
            reject_field_origins = {
                f: ("caller" if f in specified else "inherited")
                for rid in reject_rule_ids
                for f in governing_fields.get(rid, ())
            }
            # Preserved verbatim (no shadow-logging regression). Now also
            # reached on the hard_422 path because it sits above the raise below.
            logger.info(
                "supersede would_reject rule_ids=%s derivation_type=%s force=%s "
                "valid_from_inherited=%s parent_had_valid_from=%s reject_field_origins=%s",
                reject_rule_ids,
                eff_derivation_type,
                body.force,
                "valid_from" not in specified,
                old_data.get("valid_from") is not None,
                reject_field_origins,
            )
            # Durable Event-Service signal — fires on BOTH shadow and hard_422
            # paths (emitted before the 422 raise). Fire-and-forget; never blocks.
            cortex_supersede_would_reject(
                rule_ids=reject_rule_ids,
                derivation_type=eff_derivation_type,
                force=body.force,
                valid_from_inherited="valid_from" not in specified,
                parent_had_valid_from=old_data.get("valid_from") is not None,
                reject_field_origins=reject_field_origins,
                mode=mode,
                entity_id=body.entity_id,
            )
            if mode == "hard_422":
                diagnostics = [
                    {"field": d.field, "message": d.message}
                    for d in validation.hard_reject
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
            quality_validation_warnings.append(
                {
                    "field": "assertion_quality",
                    "category": "would_reject",
                    "message": (
                        "would_reject: quality rules "
                        f"{', '.join(reject_rule_ids)} would block in hard_422 mode"
                    ),
                }
            )

        if validation.route_to_staging:
            staging_rule_ids = _staging_rule_ids(
                validation.warnings, validation.quality_score
            )
            logger.info(
                "supersede_route_to_staging_suppressed rule_ids=%s quality_score=%.2f",
                staging_rule_ids,
                validation.quality_score,
            )
            quality_validation_warnings.extend(
                {
                    "field": d.field,
                    "category": d.category,
                    "message": d.message,
                }
                for d in validation.warnings
            )

        # C1: Validate supersession target against semantic impact
        impact = analyze_assertion_impact(
            conn, body.entity_id, body.claim, body.confidence
        )
        touched_ids = {t.assertion_id for t in impact.touched_assertions}
        impact_warning: str | None = None
        if (
            body.old_assertion_id not in impact.likely_supersedes
            and body.old_assertion_id not in touched_ids
        ):
            impact_warning = (
                f"Assertion {body.old_assertion_id} not found in semantic "
                f"impact analysis — target may not be the most relevant match"
            )
            logger.warning(
                "Supersede target %d has low semantic relevance to new claim",
                body.old_assertion_id,
            )

        entrenchment = compute_entrenchment(
            confidence=body.confidence,
            derivation_type=eff_derivation_type,
            observed_at=now,
            created_at=None,
            entity_id=body.entity_id,
            conn=conn,
        )

        eff_revision_type = body.revision_type or "restatement"
        eff_attributes = {**(body.attributes or {}), "revision_type": eff_revision_type}

        with WRITE_LOCK:
            cur = conn.execute(
                "INSERT INTO assertions ("
                "  entity_id, claim, confidence, evidence, evidence_uris,"
                "  derivation_type, observed_at, valid_from, entrenchment_score,"
                "  reasoning_summary, seeded_by, chunk_id, confidence_score,"
                "  quality_score, predicate_form, raw_predicate_form, normalization_decision,"
                "  candidate_set_fingerprint, normalizer_version, attributes"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    body.entity_id,
                    body.claim,
                    body.confidence,
                    body.evidence,
                    json_encode(eff_evidence_uris),
                    eff_derivation_type,
                    now,
                    eff_valid_from,
                    entrenchment,
                    eff_reasoning_summary,
                    eff_seeded_by,
                    eff_chunk_id,
                    eff_confidence_score,
                    validation.quality_score,
                    eff_predicate_form,
                    raw_pf,
                    norm_dec,
                    cand_fp,
                    norm_ver,
                    json_encode(eff_attributes),
                ),
            )
            new_id = cur.lastrowid

            # Atomic compare-and-swap on superseded_by: by tightening the
            # WHERE clause to require `superseded_by IS NULL`, two
            # concurrent supersede passes against the same old_assertion_id
            # cannot both succeed — the second update finds 0 rows and we
            # rollback the just-inserted replacement, preventing the silent
            # lineage clobber that produced the C1 corruption. The
            # `force=True` escape hatch widens the WHERE to permit
            # known-intentional chain rewrites. See
            # todo:cortex-superseded-by-overwrite-guards / friction 9824.
            update_where = "WHERE id = ?"
            update_params: tuple[object, ...] = (
                now,
                new_id,
                now,
                body.old_assertion_id,
            )
            if not body.force:
                update_where += " AND superseded_by IS NULL"
            update_cur = conn.execute(
                "UPDATE assertions SET valid_until = ?, superseded_by = ?, updated_at = ? "
                + update_where,
                update_params,
            )
            if update_cur.rowcount == 0:
                conn.rollback()
                conflict_rows = query(
                    conn,
                    "SELECT superseded_by FROM assertions WHERE id = ?",
                    (body.old_assertion_id,),
                )
                # Empty conflict_rows ⇒ target was deleted between the
                # pre-WRITE_LOCK 404 check and the CAS UPDATE. Surface 404,
                # not 409 — force=true would not recover a vanished row.
                if not conflict_rows:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=(
                            f"Assertion {body.old_assertion_id} no longer "
                            f"exists (deleted concurrently)"
                        ),
                    )
                existing = conflict_rows[0].get("superseded_by")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Assertion {body.old_assertion_id} is already superseded "
                        f"by {existing}; pass force=true to override"
                    ),
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

            # Fork D write side: supersession changes the active backing set
            # (old assertion closed, replacement opened), which may flip the
            # entity's derived confidence-axis status. Recompute in-transaction.
            recompute_entity_substantiation_status(conn, body.entity_id)

            conn.commit()

        enrich_old_assertion_events(conn, body.old_assertion_id)

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

    threading.Thread(target=reindex_assertion_fts, args=(new_id,), daemon=True).start()
    dispatch_assertion_enrichment_background(
        new_id, body.claim, body.entity_id, body.confidence
    )

    # Re-derive predicate_form from the new claim when the old form was dropped
    # (claim changed, no explicit supply). Mirrors the create path's async
    # extract so the new row's predicate_form reflects its own claim rather
    # than the predecessor's. Best-effort; the pipeline is idempotent.
    if redrive_predicate_extract:
        dispatch_predicate_extract_background(new_id, body.claim, body.entity_id)

    if vector_store.is_initialized():
        vector_store.delete_assertion_embedding(body.old_assertion_id)
    _embed_assertion_background(
        new_id,
        {
            "claim": body.claim,
            "entity_id": body.entity_id,
            "confidence": body.confidence,
            "derivation_type": eff_derivation_type,
            "entrenchment_score": entrenchment,
            "observed_at": now,
            "prospective_summary": None,
            "events_json": None,
        },
    )

    auditor_warnings = check_confirmed_validatability(
        confidence=body.confidence,
        evidence_uris=eff_evidence_uris,
        derivation_type=eff_derivation_type,
        claim=body.claim,
        acknowledge_audit_gaps=body.acknowledge_audit_gaps,
    )
    brevity_warnings = check_claim_brevity(
        claim=body.claim,
        evidence_uris=eff_evidence_uris,
        entity_id=body.entity_id,
        acknowledge_audit_gaps=body.acknowledge_audit_gaps,
    )
    provenance_warnings = check_derived_extract_primary(
        eff_evidence_uris
    ) + check_chunk_locality(
        derivation_type=eff_derivation_type,
        claim=body.claim,
        evidence_uris=eff_evidence_uris,
        chunk_id=eff_chunk_id,
    )
    combined_warnings = (
        (quality_validation_warnings or [])
        + (auditor_warnings or [])
        + (brevity_warnings or [])
        + (provenance_warnings or [])
    )

    return SupersedeResponse(
        old=AssertionItem(**decode_row(old_result[0], _JSON_FIELDS)),
        new=AssertionItem(**decode_row(new_result[0], _JSON_FIELDS)),
        impact_warning=impact_warning,
        validation_warnings=combined_warnings or None,
    )


def _supersede_assertion_impl(payload: dict[str, object]) -> dict[str, object]:
    try:
        body = SupersedeRequest.model_validate(payload)
    except ValidationError as exc:
        raise _payload_validation_exception(exc) from exc
    result = supersede_assertion(body)
    return result.model_dump(mode="json")


__all__ = ["_supersede_assertion_impl", "supersede_assertion"]
