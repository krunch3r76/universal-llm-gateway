"""GET /assertions/entrenchment — list ordered by entrenchment score
(descending). The belief base for an entity ordered by resistance to
contraction. K÷7 (Superexpansion): lower-entrenchment beliefs contract first.
"""

from __future__ import annotations

from fastapi import Query

from ...db import cortex_conn, decode_row, query
from ...models import AssertionItem, AssertionList
from ._shared import _ASSERTION_COLS, _JSON_FIELDS, logger, router


@router.get("/entrenchment", response_model=AssertionList)
def list_assertions_by_entrenchment(
    entity_id: str = Query(..., description="Entity to list assertions for"),
    superseded: bool = Query(False, description="Include superseded assertions"),
    limit: int = Query(50, ge=1, le=500),
) -> AssertionList:
    """List assertions ordered by entrenchment score (descending)."""
    clauses: list[str] = ["entity_id = ?"]
    params: list[str | int] = [entity_id]

    if not superseded:
        clauses.append("superseded_by IS NULL")

    where = " AND ".join(clauses)
    sql = (
        f"SELECT {_ASSERTION_COLS} FROM assertions "
        f"WHERE {where} "
        "ORDER BY COALESCE(entrenchment_score, 0.0) DESC LIMIT ?"
    )
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


__all__ = ["list_assertions_by_entrenchment"]
