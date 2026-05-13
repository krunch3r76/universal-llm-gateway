"""POST /assertions/{id}/enrich — explicitly trigger enrichment on an
existing assertion. Synchronous; updates the assertion row before returning.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from ...db import cortex_conn, decode_row, query
from ...enrichment import enrich_assertion
from ...models import AssertionItem, EnrichRequest, EnrichResponse
from ._shared import _ASSERTION_COLS, _JSON_FIELDS, router


@router.post("/{assertion_id}/enrich", response_model=EnrichResponse)
def enrich_assertion_endpoint(
    assertion_id: int, body: EnrichRequest | None = None
) -> EnrichResponse:
    """Explicitly trigger enrichment on an existing assertion.

    Accepts an optional list of enrichment kinds (``prospective``, ``events``).
    Defaults to all available enrichments. Runs synchronously and updates the
    assertion row before returning.
    """
    with cortex_conn() as conn:
        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assertion not found: {assertion_id}",
        )

    row = decode_row(rows[0], _JSON_FIELDS)
    kinds = {"prospective", "events"}
    if body and body.enrichments:
        kinds = set(body.enrichments)

    results = enrich_assertion(
        assertion_id,
        row["claim"],
        row["entity_id"],
        row["confidence"],
        kinds=kinds,
    )

    with cortex_conn() as conn:
        updated_rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )

    item = AssertionItem(**decode_row(updated_rows[0], _JSON_FIELDS))
    return EnrichResponse(
        item=item,
        enrichments_run=sorted(kinds),
        results=results,
    )


__all__ = ["enrich_assertion_endpoint"]
