"""GET /boot-recent-work — plan phases and in-flight todos for boot briefings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...db import cortex_conn
from ...db import query as db_query

router = APIRouter(tags=["boot"])

_BOOT_PLAN_PHASES_SQL = """
    SELECT e.id, e.name, e.workflow_state,
           json_extract(e.attributes, '$.plan_id') AS plan_id,
           json_extract(e.attributes, '$.phase_number') AS phase_number,
           e.description, e.updated_at
    FROM entities e
    WHERE e.type = 'plan_phase'
      AND e.workflow_state IN ('in_progress', 'done')
    ORDER BY e.updated_at DESC
    LIMIT ?
"""

_BOOT_IN_FLIGHT_TODOS_SQL = """
    SELECT e.id, e.name,
           json_extract(e.attributes, '$.priority') AS priority,
           json_extract(e.attributes, '$.domain') AS domain,
           e.description, e.updated_at
    FROM entities e
    WHERE e.type = 'todo'
      AND e.workflow_state = 'in_progress'
    ORDER BY e.updated_at DESC
    LIMIT ?
"""


@router.get("/boot-recent-work")
def get_boot_recent_work(
    phase_limit: int = Query(3, ge=1, le=10, description="Max plan phases to return"),
    todo_limit: int = Query(
        5, ge=1, le=20, description="Max in-flight todos to return"
    ),
) -> dict[str, Any]:
    """Recent work trail for boot briefings.

    plan_phases: last N plan_phase entities in in_progress or done state,
    ordered by most recently updated.  Gives agents a quick view of where
    multi-phase implementation plans currently stand.

    in_flight_todos: todos with workflow_state=in_progress, limit todo_limit.
    Surfaced alongside plan phases so Universal Mode and Continue Mode agents
    both receive a consistent work-trail without separate boot queries.
    """
    with cortex_conn() as conn:
        phase_rows = db_query(conn, _BOOT_PLAN_PHASES_SQL, (phase_limit,))
        todo_rows = db_query(conn, _BOOT_IN_FLIGHT_TODOS_SQL, (todo_limit,))

    plan_phases = [
        {
            "id": r["id"],
            "name": r["name"],
            "workflow_state": r["workflow_state"],
            "plan_id": r["plan_id"],
            "phase_number": r["phase_number"],
            "description": (r["description"] or "")[:200],
            "updated_at": r["updated_at"],
        }
        for r in phase_rows
    ]

    in_flight_todos = [
        {
            "id": r["id"],
            "name": r["name"],
            "priority": r["priority"],
            "domain": r["domain"],
            "description": (r["description"] or "")[:200],
            "updated_at": r["updated_at"],
        }
        for r in todo_rows
    ]

    return {
        "plan_phases": plan_phases,
        "in_flight_todos": in_flight_todos,
    }
