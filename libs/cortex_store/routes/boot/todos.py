"""GET /boot-todos — open todo entities for boot briefings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...db import cortex_conn
from ...db import query as db_query

router = APIRouter(tags=["boot"])

_BOOT_TODOS_SQL = """
    SELECT e.id, e.name,
           json_extract(e.attributes, '$.priority') as priority,
           json_extract(e.attributes, '$.domain') as domain,
           json_extract(e.attributes, '$.context') as context,
           e.description, e.source_uri
    FROM entities e
    WHERE e.type = 'todo'
    AND e.workflow_state = 'open'
    {context_filter}
    {domain_filter}
    ORDER BY
        CASE json_extract(e.attributes, '$.priority')
            WHEN 'high' THEN 1
            WHEN 'medium' THEN 2
            ELSE 3
        END,
        e.updated_at DESC
    LIMIT ?
"""


@router.get("/boot-todos")
def get_boot_todos(
    limit: int = Query(15, ge=1, le=50, description="Max open todos"),
    context: str | None = Query(
        None, description="Filter by context (e.g. 'code'). None = all."
    ),
    domain_exclude: str | None = Query(
        None,
        description="Comma-separated domains to exclude (e.g. 'infra,rag,pipeline').",
    ),
) -> dict[str, Any]:
    """Open todo entities for boot briefings, priority-ordered.

    Cursor boot passes context=code to exclude personal/financial/legal todos.
    Web boot passes domain_exclude to filter out infra/rag/pipeline/mcp todos.
    """
    params: list[str | int] = []
    if context:
        context_filter = "AND json_extract(e.attributes, '$.context') = ?"
        params.append(context)
    else:
        context_filter = ""

    domain_filter = ""
    if domain_exclude:
        excluded = [d.strip() for d in domain_exclude.split(",") if d.strip()]
        if excluded:
            placeholders = ",".join("?" * len(excluded))
            domain_filter = (
                f"AND (json_extract(e.attributes, '$.domain') IS NULL "
                f"OR json_extract(e.attributes, '$.domain') NOT IN ({placeholders}))"
            )
            params.extend(excluded)

    params.append(limit)

    sql = _BOOT_TODOS_SQL.format(
        context_filter=context_filter, domain_filter=domain_filter
    )
    conn = cortex_conn()
    try:
        rows = db_query(conn, sql, tuple(params))
    finally:
        conn.close()
    items = [
        {
            "id": r["id"],
            "title": r["name"],
            "priority": r["priority"],
            "domain": r["domain"],
            "context": r["context"],
            "description": r["description"],
            "source_uri": r["source_uri"],
        }
        for r in rows
    ]
    return {"items": items}
