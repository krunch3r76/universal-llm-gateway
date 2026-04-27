"""GET /boot-recent-mentions — entities recently mentioned in session work."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...db import cortex_conn
from ...db import query as db_query

router = APIRouter(tags=["boot"])

# Recent mentions: entities with new assertions OR newly-created entities within
# trailing window. Surfaces names that came up in session work so boot agents
# recognize them without re-derivation. Noisy system types are excluded by
# default; callers may override via type_exclude.
_RECENT_MENTIONS_DEFAULT_EXCLUDE = ("transcript", "todo", "journal", "assertion")

_RECENT_MENTIONS_SQL = """
    SELECT
        e.id AS entity_id,
        e.name AS entity_name,
        e.type AS entity_type,
        e.created_at AS entity_created_at,
        COUNT(a.id) AS recent_mention_count,
        MAX(COALESCE(a.created_at, e.created_at)) AS last_mentioned_at
    FROM entities e
    LEFT JOIN assertions a
        ON a.entity_id = e.id
        AND a.created_at > datetime('now', ?)
        AND a.superseded_by IS NULL
    WHERE (
            e.created_at > datetime('now', ?)
            OR a.id IS NOT NULL
          )
      AND (e.status IS NULL OR e.status != 'deprecated')
      {type_filter}
    GROUP BY e.id
    ORDER BY last_mentioned_at DESC
    LIMIT ?
"""


@router.get("/boot-recent-mentions")
def get_boot_recent_mentions(
    days: int = Query(7, ge=1, le=30, description="Trailing window in days"),
    limit: int = Query(10, ge=1, le=30, description="Max entities"),
    type_exclude: str | None = Query(
        None,
        description=(
            "Comma-separated entity types to exclude. "
            "Defaults to 'transcript,todo,journal,assertion' "
            "(system/meta types already surfaced elsewhere)."
        ),
    ),
) -> dict[str, Any]:
    """Entities recently mentioned via new assertions or new entity creation.

    Surfaces a roster of names that came up in trailing session work so the
    boot agent recognizes them without re-derivation. Covers the case where
    Kaywan references a person/organization that was introduced in a prior
    session — the entity exists in the graph, but the boot card previously
    had no way to surface it unless it appeared in another section (deadlines,
    todos, etc.).

    Default window: 7 days. Default exclusions: transcript, todo, journal,
    assertion (already surfaced elsewhere or noisy).
    """
    if type_exclude is None:
        excluded = list(_RECENT_MENTIONS_DEFAULT_EXCLUDE)
    else:
        excluded = [t.strip() for t in type_exclude.split(",") if t.strip()]

    type_filter = ""
    params: list[Any] = [f"-{days} days", f"-{days} days"]
    if excluded:
        placeholders = ",".join("?" * len(excluded))
        type_filter = f"AND e.type NOT IN ({placeholders})"
        params.extend(excluded)
    params.append(limit)

    sql = _RECENT_MENTIONS_SQL.format(type_filter=type_filter)
    conn = cortex_conn()
    try:
        rows = db_query(conn, sql, tuple(params))
    finally:
        conn.close()

    items = [
        {
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "entity_type": r["entity_type"],
            "recent_mention_count": r["recent_mention_count"],
            "last_mentioned_at": r["last_mentioned_at"],
            "entity_created_at": r["entity_created_at"],
        }
        for r in rows
    ]
    return {"items": items, "window_days": days, "excluded_types": excluded}
