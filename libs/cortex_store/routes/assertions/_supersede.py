"""POST /assertions/supersede — atomic close+create with clone-then-override
field carryover, C1 semantic-impact check, auditor-validatability warnings,
and supersession-edge bookkeeping.
"""

from __future__ import annotations

import datetime as dt
import threading

from fastapi import HTTPException, status
from pydantic import ValidationError

from ... import vector_store
from ...assertion_quality import check_confirmed_validatability
from ...belief_guard import analyze_assertion_impact
from ...db import WRITE_LOCK, cortex_conn, decode_row, json_encode, query
from ...enrichment import (
    enrich_background,
    enrich_old_assertion_events,
    reindex_assertion_fts,
)
from ...entrenchment import compute_entrenchment
from ...models import (
    AssertionItem,
    SupersedeRequest,
    SupersedeResponse,
)
from ._shared import (
    _ASSERTION_COLS,
    _JSON_FIELDS,
    _VALID_CONFIDENCE,
    _embed_assertion_background,
    _payload_validation_exception,
    logger,
    router,
)


@router.post(
    "/supersede", response_model=SupersedeResponse, status_code=status.HTTP_201_CREATED
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
        eff_derivation_type: str = _resolve("derivation_type") or "inference"  # type: ignore[assignment]
        eff_valid_from: str | None = _resolve("valid_from")  # type: ignore[assignment]
        eff_reasoning_summary: str | None = _resolve("reasoning_summary")  # type: ignore[assignment]
        eff_seeded_by: str | None = _resolve("seeded_by")  # type: ignore[assignment]
        eff_chunk_id: int | None = _resolve("chunk_id")  # type: ignore[assignment]
        eff_confidence_score: float | None = _resolve("confidence_score")  # type: ignore[assignment]

        entities = query(
            conn, "SELECT id FROM entities WHERE id = ?", (body.entity_id,)
        )
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {body.entity_id}",
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

        with WRITE_LOCK:
            cur = conn.execute(
                "INSERT INTO assertions ("
                "  entity_id, claim, confidence, evidence, evidence_uris,"
                "  derivation_type, observed_at, valid_from, entrenchment_score,"
                "  reasoning_summary, seeded_by, chunk_id, confidence_score"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    enrich_background(new_id, body.claim, body.entity_id, body.confidence)

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

    return SupersedeResponse(
        old=AssertionItem(**decode_row(old_result[0], _JSON_FIELDS)),
        new=AssertionItem(**decode_row(new_result[0], _JSON_FIELDS)),
        impact_warning=impact_warning,
        validation_warnings=auditor_warnings or None,
    )


def _supersede_assertion_impl(payload: dict[str, object]) -> dict[str, object]:
    try:
        body = SupersedeRequest.model_validate(payload)
    except ValidationError as exc:
        raise _payload_validation_exception(exc) from exc
    result = supersede_assertion(body)
    return result.model_dump(mode="json")


__all__ = ["_supersede_assertion_impl", "supersede_assertion"]
