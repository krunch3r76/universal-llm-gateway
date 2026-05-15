"""PATCH /assertions/{id} — update metadata (supersession, confidence,
review status, predicate writeback)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from ...db import WRITE_LOCK, cortex_conn, decode_row, query
from ...models import AssertionItem, AssertionUpdate
from ._shared import (
    _ASSERTION_COLS,
    _JSON_FIELDS,
    _VALID_CONFIDENCE,
    _VALID_REVIEW_STATUS,
    _payload_validation_exception,
    logger,
    router,
)


@router.patch("/{assertion_id}", response_model=AssertionItem)
def update_assertion(
    assertion_id: int, body: AssertionUpdate | dict[str, Any]
) -> AssertionItem:
    """Update assertion metadata — supersession, confidence, review status."""
    with cortex_conn() as conn:
        existing = query(
            conn,
            "SELECT id, predicate_form FROM assertions WHERE id = ?",
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
        else:
            superseded_by = body.superseded_by
            review_status = body.review_status
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
            ):
                if k in body and body[k] is not None:
                    update_map[k] = body[k]
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
            ):
                val = getattr(body, k)
                if val is not None:
                    update_map[k] = val
            # predicate_form: use model_fields_set to detect explicit null (clearing)
            if "predicate_form" in body.model_fields_set:
                update_map["predicate_form"] = body.predicate_form
                predicate_form_explicitly_set = True
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

        with WRITE_LOCK:
            conn.execute(
                f"UPDATE assertions SET {', '.join(sets)} WHERE id = ?", tuple(params)
            )
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

        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )

    return AssertionItem(**decode_row(rows[0], _JSON_FIELDS))


def _update_assertion_impl(
    assertion_id: int, payload: dict[str, object]
) -> dict[str, object]:
    try:
        body = AssertionUpdate.model_validate(payload)
    except ValidationError as exc:
        raise _payload_validation_exception(exc) from exc
    result = update_assertion(assertion_id, body)
    return result.model_dump(mode="json")


__all__ = ["_update_assertion_impl", "update_assertion"]
