"""GET /boot-commitments — open commitments for boot briefings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...db import cortex_conn
from ...db import query as db_query

router = APIRouter(tags=["boot"])

_COMMITMENTS_SQL = """
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim,
           a.confidence, a.valid_from, a.valid_until,
           a.resolution_status, a.created_at
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE a.resolution_status = 'pending'
      AND a.superseded_by IS NULL
      AND (a.valid_until IS NULL OR a.valid_until > datetime('now'))
    ORDER BY a.valid_from ASC
    LIMIT ?
"""


@router.get("/boot-commitments")
def get_boot_commitments(
    limit: int = Query(10, ge=1, le=50, description="Max open commitments"),
) -> dict[str, Any]:
    """Open commitments (resolution_status='pending') for boot briefings.

    Returns count + top N by age (oldest first). Used by cortex_boot
    to surface unresolved promises alongside open investigations.
    """
    conn = cortex_conn()
    try:
        rows = db_query(conn, _COMMITMENTS_SQL, (limit,))
    finally:
        conn.close()

    items = [
        {
            "id": r["id"],
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "claim": r["claim"],
            "confidence": r["confidence"],
            "valid_from": r["valid_from"],
            "valid_until": r["valid_until"],
            "resolution_status": r["resolution_status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}
