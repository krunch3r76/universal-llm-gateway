"""Open task: arcs with child_of leaf todos for GET /boot-recent-work."""

from __future__ import annotations

from typing import Any

from ...db import query as db_query

_BOOT_OPEN_ARCS_TASKS_SQL = """
    SELECT e.id, e.name, e.workflow_state, e.updated_at
    FROM entities e
    WHERE e.type = 'task'
      AND e.workflow_state IN ('open', 'in_progress')
    ORDER BY e.updated_at DESC
    LIMIT ?
"""

_BOOT_ARC_CHILDREN_SQL = """
    SELECT c.id, c.workflow_state
    FROM relationships r
    JOIN entities c ON c.id = r.from_entity AND c.type = 'todo'
    WHERE r.to_entity = ?
      AND r.type = 'child_of'
      AND r.active = 1
      AND r.valid_until IS NULL
    ORDER BY c.workflow_state, c.id
"""

_CHILD_DISPLAY_CAP = 8


def fetch_open_arcs(conn: Any, *, arc_limit: int) -> list[dict[str, Any]]:
    """Return open/in_progress task arcs with active child_of leaf todos."""
    task_rows = db_query(conn, _BOOT_OPEN_ARCS_TASKS_SQL, (arc_limit,))
    arcs: list[dict[str, Any]] = []
    for row in task_rows:
        child_rows = db_query(conn, _BOOT_ARC_CHILDREN_SQL, (row["id"],))
        arcs.append(
            {
                "id": row["id"],
                "name": row["name"],
                "workflow_state": row["workflow_state"],
                "updated_at": row["updated_at"],
                "children": [
                    {"id": c["id"], "workflow_state": c["workflow_state"]}
                    for c in child_rows
                ],
            }
        )
    return arcs


def format_open_arc_line(arc: dict[str, Any]) -> str:
    """Compact single-line arc summary for the briefing card."""
    children = list(arc.get("children") or [])
    n = len(children)
    if n == 0:
        child_suffix = "0 leaf todos"
    else:
        labels = [
            _short_entity_id(c.get("id", "?")) for c in children[:_CHILD_DISPLAY_CAP]
        ]
        tail = f" +{n - _CHILD_DISPLAY_CAP} more" if n > _CHILD_DISPLAY_CAP else ""
        child_suffix = f"{n} leaf todos ({', '.join(labels)}{tail})"
    state = arc.get("workflow_state", "?")
    return f"- `{arc.get('id', '?')}` [{state}] — {child_suffix}"


def _short_entity_id(entity_id: str) -> str:
    if ":" in entity_id:
        return entity_id.split(":", 1)[1]
    return entity_id
