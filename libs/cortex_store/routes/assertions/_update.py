"""PATCH /assertions/{id} — update metadata (supersession, confidence,
review status, predicate writeback)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from ...db import WRITE_LOCK, cortex_conn, decode_row, query
from ...enrichment import reindex_assertion_fts
from ...models import AssertionItem, AssertionUpdate, AssertionUpdateResponse
from ...status_trait_write import materialize_graduated_lifecycle
from ._shared import (
    _ASSERTION_COLS,
    _JSON_FIELDS,
    _VALID_CONFIDENCE,
    _VALID_REVIEW_STATUS,
    _build_predicate_form_normalize,
    _flag_predicate_normalize_review,
    _normalize_predicate_form_for_write,
    _payload_validation_exception,
    logger,
    router,
)


@router.patch("/{assertion_id}", response_model=AssertionUpdateResponse)
def update_assertion(
    assertion_id: int, body: AssertionUpdate | dict[str, Any]
) -> AssertionUpdateResponse:
    """Update assertion metadata — supersession, confidence, review status.

    Idempotency guard on superseded_by: when superseded_by is being set and
    the target row's superseded_by is already non-null, the PATCH returns
    409 Conflict; the lineage pointer is preserved. Pass force=true to
    widen the SQL CAS and overwrite a known-existing supersedence chain.
    See decision:cortex-api-write-serialization / assertion 9956 for the
    WRITE_LOCK-vs-SQL-CAS doctrine and friction 9825 for the C1 trigger.
    """
    with cortex_conn() as conn:
        existing = query(
            conn,
            "SELECT id, entity_id, claim, predicate_form FROM assertions WHERE id = ?",
            (assertion_id,),
        )
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Assertion not found: {assertion_id}",
            )

        if isinstance(body, dict):
            superseded_by = body.get("superseded_by")
            review_status = body.get("review_status")
            force = bool(body.get("force", False))
        else:
            superseded_by = body.superseded_by
            review_status = body.review_status
            force = bool(getattr(body, "force", False))
        if superseded_by is not None:
            target = query(
                conn, "SELECT id FROM assertions WHERE id = ?", (superseded_by,)
            )
            if not target:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Superseding assertion not found: {superseded_by}",
                )

        if review_status is not None and review_status not in _VALID_REVIEW_STATUS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid review_status: {review_status!r}. "
                f"Must be one of {sorted(_VALID_REVIEW_STATUS)}",
            )

        confidence = (
            body.get("confidence")
            if isinstance(body, dict)
            else getattr(body, "confidence", None)
        )
        if confidence is not None and confidence not in _VALID_CONFIDENCE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid confidence: {confidence!r}. "
                f"Must be one of {sorted(_VALID_CONFIDENCE)}",
            )

        old_predicate_form = existing[0].get("predicate_form")

        update_map: dict[str, object] = {}
        # predicate_form_explicitly_set: True when the caller explicitly
        # included predicate_form in the request (even as null = clearing intent).
        predicate_form_explicitly_set = False
        enrichment_fields_updated = False
        if isinstance(body, dict):
            for k in (
                "superseded_by",
                "valid_until",
                "confidence",
                "confidence_score",
                "review_status",
                "reviewer",
                "reviewed_at",
                "review_notes",
                "resolution_status",
                "fulfillment_assertion_id",
                "prospective_summary",
                "events_json",
            ):
                if k in body and body[k] is not None:
                    update_map[k] = body[k]
                    if k in ("prospective_summary", "events_json"):
                        enrichment_fields_updated = True
            # predicate_form: explicit null in dict → clear the field
            if "predicate_form" in body:
                update_map["predicate_form"] = body["predicate_form"]
                predicate_form_explicitly_set = True
        else:
            for k in (
                "superseded_by",
                "valid_until",
                "confidence",
                "confidence_score",
                "review_status",
                "reviewer",
                "reviewed_at",
                "review_notes",
                "resolution_status",
                "fulfillment_assertion_id",
                "prospective_summary",
                "events_json",
            ):
                val = getattr(body, k)
                if val is not None:
                    update_map[k] = val
                    if k in ("prospective_summary", "events_json"):
                        enrichment_fields_updated = True
            # predicate_form: use model_fields_set to detect explicit null (clearing)
            if "predicate_form" in body.model_fields_set:
                update_map["predicate_form"] = body.predicate_form
                predicate_form_explicitly_set = True
        # v1.3 Q5: normalize predicate_form before UPDATE.
        # Q5.4 always-re-normalize — runs even when value looks canonical.
        # Outside WRITE_LOCK (DBEntityResolver reads entities.id, no writes).
        normalize_result: dict | None = None
        predicate_form_in_for_event: str | None = None
        if (
            predicate_form_explicitly_set
            and update_map.get("predicate_form") is not None
        ):
            entity_id_for_norm = str(existing[0].get("entity_id") or "")
            claim_for_norm = str(existing[0].get("claim") or "")
            predicate_form_in_for_event = str(update_map["predicate_form"])
            canonical, normalize_result = _normalize_predicate_form_for_write(
                entity_id_for_norm,
                predicate_form_in_for_event,
                claim_for_norm,
                conn,
            )
            update_map["predicate_form"] = canonical
            update_map["raw_predicate_form"] = normalize_result.get(
                "raw_predicate_form"
            )
            update_map["normalization_decision"] = normalize_result.get(
                "normalization_decision"
            )
            update_map["candidate_set_fingerprint"] = normalize_result.get(
                "candidate_set_fingerprint"
            )
            update_map["normalizer_version"] = normalize_result.get(
                "normalizer_version"
            )

        sets: list[str] = []
        params: list[object] = []
        for col, val in update_map.items():
            # update_map already enforces inclusion semantics:
            # - non-predicate-form cols are added only when val is not None
            # - predicate_form is added iff predicate_form_explicitly_set
            # so every entry in update_map should land in SET.
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

        # Atomic compare-and-swap when setting superseded_by: by tightening
        # the WHERE clause to require `superseded_by IS NULL`, two
        # concurrent writers cannot both clobber the lineage pointer — the
        # second update finds 0 rows and we surface a 409. The
        # `force=True` escape hatch widens the WHERE to permit
        # known-intentional chain rewrites. See
        # todo:cortex-superseded-by-overwrite-guards / friction 9825.
        where_clause = "WHERE id = ?"
        if superseded_by is not None and not force:
            where_clause += " AND superseded_by IS NULL"

        with WRITE_LOCK:
            cur = conn.execute(
                f"UPDATE assertions SET {', '.join(sets)} {where_clause}",
                tuple(params),
            )
            if cur.rowcount == 0 and superseded_by is not None and not force:
                conflict_rows = query(
                    conn,
                    "SELECT superseded_by FROM assertions WHERE id = ?",
                    (assertion_id,),
                )
                # Empty conflict_rows ⇒ row was deleted between the
                # pre-WRITE_LOCK 404 check and the CAS UPDATE. Surface 404,
                # not 409 — force=true would not recover a vanished row.
                if not conflict_rows:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=(
                            f"Assertion {assertion_id} no longer exists "
                            f"(deleted concurrently)"
                        ),
                    )
                existing_superseded_by = conflict_rows[0].get("superseded_by")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Assertion {assertion_id} is already superseded by "
                        f"{existing_superseded_by}; pass force=true to override"
                    ),
                )

            # Q5.2=(c): flag if normalize found review-worthy form.
            if normalize_result and normalize_result.get("requires_human_review"):
                _flag_predicate_normalize_review(conn, assertion_id, normalize_result)
            if review_status == "committed":
                entity_id = str(existing[0].get("entity_id") or "")
                if entity_id:
                    materialize_graduated_lifecycle(conn, entity_id)
            conn.commit()

        if predicate_form_explicitly_set:
            new_predicate_form = (
                body.get("predicate_form")
                if isinstance(body, dict)
                else body.predicate_form
            )
            logger.info(
                "cortex assertion_update predicate_form delta: id=%d old=%r new=%r",
                assertion_id,
                old_predicate_form,
                new_predicate_form,
            )

        if enrichment_fields_updated:
            reindex_assertion_fts(assertion_id)

        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )

    item = AssertionItem(**decode_row(rows[0], _JSON_FIELDS))
    predicate_form_normalize_out = None
    if normalize_result is not None and predicate_form_in_for_event is not None:
        predicate_form_normalize_out = _build_predicate_form_normalize(
            predicate_form_in_for_event, normalize_result
        )
    return AssertionUpdateResponse(
        item=item, predicate_form_normalize=predicate_form_normalize_out
    )


def _update_assertion_impl(
    assertion_id: int, payload: dict[str, object]
) -> dict[str, object]:
    try:
        body = AssertionUpdate.model_validate(payload)
    except ValidationError as exc:
        raise _payload_validation_exception(exc) from exc
    result = update_assertion(assertion_id, body)
    # Flatten envelope at the impl boundary: dispatch_ops and existing
    # consumers read flat AssertionItem fields (id, claim, superseded_by,
    # …). The HTTP route exposes the envelope; the impl preserves the
    # flat shape with an additive sibling key. Q5.5 / dispatch packet
    # `cortex://notes/system/threads/cortex-api-event-emission-surface-dispatch.md`.
    item_dump = result.item.model_dump(mode="json")
    if result.predicate_form_normalize is not None:
        item_dump["predicate_form_normalize"] = (
            result.predicate_form_normalize.model_dump(mode="json")
        )
    return item_dump


__all__ = ["_update_assertion_impl", "update_assertion"]
