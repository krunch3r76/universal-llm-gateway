"""Server-side resolution-index source for open-items reconciliation.

Queries cortex's own tables for recently-resolved work in the unified record
shape consumed by ``reconcile.build_resolution_index``:

  * superseded assertions (any type, last N days)
  * done / cancelled todo entities (last N days)

Used by the control tower aggregation and the ``/boot-temporal`` endpoint.
Requires a live cortex DB connection — pure matching lives in ``reconcile``.
"""

from __future__ import annotations

from typing import Any

from ..db import query as db_query

# Supersession sets valid_until = now alongside superseded_by (see
# routes/assertions/_supersede.py), so "valid_until set AND superseded_by set"
# captures every recently-superseded assertion — temporal window or not.
_RESOLVED_ASSERTIONS_SQL = """
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE a.valid_until IS NOT NULL
      AND a.valid_until >= datetime('now', ?)
      AND a.superseded_by IS NOT NULL
      AND a.review_status = 'committed'
    ORDER BY a.valid_until DESC
    LIMIT ?
"""

# Todos are entities (type='todo') in the cortex DB; 'done'/'cancelled' are the
# closed workflow states. updated_at carries the close timestamp.
_RESOLVED_TODOS_SQL = """
    SELECT e.id, e.name AS entity_name, e.description AS claim
    FROM entities e
    WHERE e.type = 'todo'
      AND e.workflow_state IN ('done', 'cancelled')
      AND e.updated_at >= datetime('now', ?)
    ORDER BY e.updated_at DESC
    LIMIT ?
"""


def fetch_resolved_assertions(
    conn: Any, *, days: int = 30, limit: int = 50
) -> list[dict[str, Any]]:
    """Recently-superseded assertions as resolved records (id-keyed)."""
    rows = db_query(conn, _RESOLVED_ASSERTIONS_SQL, (f"-{days} days", limit))
    return [
        {
            "id": r["id"],
            "slug": None,
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "claim": r["claim"],
        }
        for r in rows
    ]


def fetch_resolved_todos(
    conn: Any, *, days: int = 30, limit: int = 50
) -> list[dict[str, Any]]:
    """Recently done/cancelled todos as resolved records (slug-keyed)."""
    rows = db_query(conn, _RESOLVED_TODOS_SQL, (f"-{days} days", limit))
    out: list[dict[str, Any]] = []
    for r in rows:
        entity_id = str(r["id"])
        out.append(
            {
                "id": None,
                "slug": entity_id.replace("todo:", "", 1),
                "entity_id": entity_id,
                "entity_name": r["entity_name"],
                "claim": r["claim"] or r["entity_name"],
            }
        )
    return out


def fetch_resolution_index_records(
    conn: Any,
    *,
    days: int = 30,
    assertion_limit: int = 50,
    todo_limit: int = 50,
) -> list[dict[str, Any]]:
    """Unified resolved-record set (superseded assertions + closed todos)."""
    return fetch_resolved_assertions(
        conn, days=days, limit=assertion_limit
    ) + fetch_resolved_todos(conn, days=days, limit=todo_limit)
