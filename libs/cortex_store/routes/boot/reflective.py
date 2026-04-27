"""GET /boot-reflective — recent reflective journal entries for boot briefings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...db import cortex_conn
from ...db import query as db_query
from ._render import _table_exists

router = APIRouter(tags=["boot"])

_BOOT_REFLECTIVE_SQL = """
    SELECT id, agent, register, entry, kind, session_id, created_at
    FROM reflective_journal
    WHERE agent = ?
    ORDER BY id DESC
    LIMIT ?
"""


@router.get("/boot-reflective")
def get_boot_reflective(
    agent: str = Query("web", description="Agent whose reflective entries to surface"),
    limit: int = Query(5, ge=1, le=20, description="Max entries"),
) -> dict[str, Any]:
    """Recent reflective journal entries for boot briefings.

    Surfaces the latest entries by the specified agent, ordered newest-first.
    Consolidation entries are included alongside raw entries so the boot
    can present both the living grain and any synthesized throughlines.
    """
    conn = cortex_conn()
    try:
        if not _table_exists(conn, "reflective_journal"):
            return {"items": [], "total": 0}
        rows = db_query(conn, _BOOT_REFLECTIVE_SQL, (agent, limit))
        total_row = db_query(
            conn,
            "SELECT COUNT(*) AS cnt FROM reflective_journal WHERE agent = ?",
            (agent,),
        )
        total = total_row[0]["cnt"] if total_row else 0
    finally:
        conn.close()

    items = [
        {
            "id": r["id"],
            "register": r["register"],
            "entry": r["entry"][:300],
            "kind": r["kind"],
            "session_id": r.get("session_id"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return {"items": items, "total": total}
