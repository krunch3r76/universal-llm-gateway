"""TODO audit endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..db import cortex_conn
from ..db import query as db_query
from .todo_retrieval import _todo_edge_join, _todo_filter_sql, _todo_row

router = APIRouter(tags=["todos"])


@router.get("/todo-audit")
def get_todo_audit(
    stale_days: int = Query(60, ge=1, le=3650),
    limit: int = Query(50, ge=1, le=500),
    domain: str | None = Query(None, description="Comma-separated domain filter."),
    priority: str | None = Query(None, description="Comma-separated priority filter."),
) -> dict[str, Any]:
    """Old/open TODO audit for deferral, closure, merge, or spec conversion."""
    with cortex_conn() as conn:
        clauses, params = _todo_filter_sql(
            workflow_state="open",
            priority=priority,
            domain=domain,
            domain_exclude=None,
            context=None,
        )
        edge_join = _todo_edge_join(conn)
        where = " AND ".join(clauses)
        sql = f"""
            SELECT e.id, e.name, e.description, e.workflow_state, e.source_uri,
                   e.created_at, e.updated_at,
                   json_extract(e.attributes, '$.priority') AS priority,
                   json_extract(e.attributes, '$.domain') AS domain,
                   json_extract(e.attributes, '$.context') AS context,
                   coalesce(ec.edge_count, 0) AS edge_count,
                   CAST(julianday('now') - julianday(e.updated_at) AS INT) AS age_days
            FROM entities e
            {edge_join}
            WHERE {where}
            ORDER BY
                CASE WHEN e.source_uri IS NULL THEN 0 ELSE 1 END,
                coalesce(ec.edge_count, 0),
                e.updated_at ASC
            LIMIT ?
        """
        params.append(limit)
        rows = db_query(conn, sql, tuple(params))

    items: list[dict[str, Any]] = []
    for row in rows:
        reasons: list[str] = []
        if row["age_days"] >= stale_days:
            reasons.append("stale_open")
        if row["source_uri"] is None:
            reasons.append("missing_spec")
        if row["edge_count"] == 0:
            reasons.append("unlinked")

        if "stale_open" in reasons and "missing_spec" in reasons:
            recommendation = "defer_close_or_convert_to_spec"
        elif "missing_spec" in reasons:
            recommendation = "convert_to_rich_spec_or_merge"
        elif "unlinked" in reasons:
            recommendation = "add_reasoning_edges_or_defer"
        else:
            recommendation = "review_recency"

        items.append(
            {
                **_todo_row(row),
                "age_days": row["age_days"],
                "audit_reasons": reasons,
                "recommendation": recommendation,
            }
        )

    return {"items": items, "total": len(items), "stale_days": stale_days}
