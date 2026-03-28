"""GET /boot-gated — entity gate injection for boot briefings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..db import cortex_conn
from ..db import query as db_query

router = APIRouter(tags=["boot"])

_GATED_ASSERTIONS_SQL = """
    SELECT a.id, a.entity_id, a.claim, a.confidence, a.confidence_score,
           a.evidence, a.valid_from, a.valid_until, a.review_status,
           a.reviewer, a.created_at
    FROM assertions a
    WHERE a.entity_id = ? AND a.superseded_by IS NULL
    ORDER BY
        CASE WHEN a.valid_until IS NOT NULL
              AND a.valid_until >= datetime('now')
              AND (a.valid_from IS NULL OR a.valid_from <= datetime('now'))
             THEN 0 ELSE 1 END,
        CASE WHEN a.reviewer IS NOT NULL THEN 0 ELSE 1 END,
        CASE a.confidence
             WHEN 'confirmed' THEN 0
             WHEN 'believed' THEN 1
             WHEN 'suspected' THEN 2
             ELSE 3 END,
        a.created_at DESC
    LIMIT ?
"""

_GATED_ASSERTION_COUNT_SQL = """
    SELECT entity_id, COUNT(*) AS total
    FROM assertions
    WHERE entity_id IN ({placeholders})
      AND superseded_by IS NULL
    GROUP BY entity_id
"""


@router.get("/boot-gated")
def get_boot_gated(
    entity_ids: str = Query("", description="Comma-separated entity IDs"),
    per_entity: int = Query(5, ge=1, le=20, description="Assertions per entity"),
) -> dict[str, Any]:
    """Fetch gated entities with priority-selected assertions for boot injection.

    Assertion priority: temporally active > human-reviewed > high confidence > recent.
    Returns entity metadata, selected assertions, and total assertion count so the
    agent knows when to call entity_get() for more.
    """
    ids = [eid.strip() for eid in entity_ids.split(",") if eid.strip()]
    if not ids:
        return {"entities": []}

    conn = cortex_conn()
    try:
        placeholders = ",".join("?" * len(ids))
        entity_rows = db_query(
            conn,
            f"SELECT id, type, name, description FROM entities "
            f"WHERE id IN ({placeholders})",
            tuple(ids),
        )
        entity_map = {r["id"]: r for r in entity_rows}

        count_rows = db_query(
            conn,
            _GATED_ASSERTION_COUNT_SQL.format(placeholders=placeholders),
            tuple(ids),
        )
        count_map = {r["entity_id"]: r["total"] for r in count_rows}

        entities: list[dict[str, Any]] = []
        for eid in ids:
            meta = entity_map.get(eid)
            if meta is None:
                continue
            assertions = db_query(conn, _GATED_ASSERTIONS_SQL, (eid, per_entity))
            entities.append(
                {
                    "entity_id": eid,
                    "entity_type": meta["type"],
                    "entity_name": meta["name"],
                    "description": meta.get("description"),
                    "assertions": assertions,
                    "assertion_count": count_map.get(eid, 0),
                    "assertions_shown": len(assertions),
                }
            )
    finally:
        conn.close()

    return {"entities": entities}
