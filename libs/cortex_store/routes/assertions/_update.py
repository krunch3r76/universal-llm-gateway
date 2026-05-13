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
    router,
)


@router.patch("/{assertion_id}", response_model=AssertionItem)
def update_assertion(
    assertion_id: int, body: AssertionUpdate | dict[str, Any]
) -> AssertionItem:
    """Update assertion metadata — supersession, confidence, review status."""
    with cortex_conn() as conn:
        existing = query(
            conn, "SELECT id FROM assertions WHERE id = ?", (assertion_id,)
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

        update_map: dict[str, object] = {}
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
                "predicate_form",
            ):
                if k in body and body[k] is not None:
                    update_map[k] = body[k]
        else:
            update_map = {
                "superseded_by": body.superseded_by,
                "valid_until": body.valid_until,
                "confidence": body.confidence,
                "confidence_score": body.confidence_score,
                "review_status": body.review_status,
                "reviewer": body.reviewer,
                "reviewed_at": body.reviewed_at,
                "review_notes": body.review_notes,
                "resolution_status": body.resolution_status,
                "fulfillment_assertion_id": body.fulfillment_assertion_id,
                "predicate_form": body.predicate_form,
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

        with WRITE_LOCK:
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
