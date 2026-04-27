"""GET /boot-legal-contacts — entities connected to active legal matters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...db import cortex_conn
from ...db import query as db_query

router = APIRouter(tags=["boot"])

_LEGAL_CONTACTS_SQL = """
    SELECT DISTINCT se.from_node AS connected_id
    FROM session_edges se
    WHERE se.to_node LIKE 'legal_matter:%'
      AND se.valid_until IS NULL
      AND se.edge_type NOT IN ('supersedes', 'superseded_by')
    UNION
    SELECT DISTINCT se.to_node AS connected_id
    FROM session_edges se
    WHERE se.from_node LIKE 'legal_matter:%'
      AND se.valid_until IS NULL
      AND se.edge_type NOT IN ('supersedes', 'superseded_by')
"""

_ENTITY_RECENT_ASSERTIONS_SQL = """
    SELECT a.entity_id, a.claim, a.confidence, a.observed_at, a.created_at
    FROM assertions a
    WHERE a.entity_id = ?
      AND a.superseded_by IS NULL
    ORDER BY a.created_at DESC
    LIMIT 3
"""


@router.get("/boot-legal-contacts")
def get_boot_legal_contacts() -> dict[str, Any]:
    """Entities connected to active legal_matter entities via reasoning edges.

    For each connected entity, returns top 3 assertions by recency.
    Used by cortex_boot to expand visibility of legal matter participants.
    """
    conn = cortex_conn()
    try:
        connected_rows = db_query(conn, _LEGAL_CONTACTS_SQL)
        connected_ids = {
            r["connected_id"]
            for r in connected_rows
            if not r["connected_id"].startswith("legal_matter:")
            and not r["connected_id"].startswith("assertion:")
        }

        contacts: list[dict[str, Any]] = []
        for entity_id in sorted(connected_ids):
            entity_rows = db_query(
                conn,
                "SELECT id, name, type FROM entities WHERE id = ?",
                (entity_id,),
            )
            if not entity_rows:
                continue

            entity = entity_rows[0]
            assertion_rows = db_query(conn, _ENTITY_RECENT_ASSERTIONS_SQL, (entity_id,))
            contacts.append(
                {
                    "entity_id": entity_id,
                    "entity_name": entity["name"],
                    "entity_type": entity["type"],
                    "assertions": [
                        {
                            "claim": a["claim"],
                            "confidence": a["confidence"],
                            "observed_at": a.get("observed_at"),
                            "created_at": a["created_at"],
                        }
                        for a in assertion_rows
                    ],
                }
            )
    finally:
        conn.close()

    return {"count": len(contacts), "contacts": contacts}
