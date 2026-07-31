"""Ranked TODO retrieval and audit endpoints."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from openapi_mcp.binding import x_mcp

from ..db import cortex_conn
from ..db import query as db_query

router = APIRouter(tags=["todos"])

_TODO_INTENT_STOPWORDS = frozenset(
    {
        "and",
        "for",
        "from",
        "into",
        "on",
        "open",
        "regarding",
        "the",
        "todo",
        "todos",
        "with",
    }
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    rows = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return rows is not None


def _csv_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _sanitize_fts_query(raw: str) -> str:
    tokens = raw.strip().split()
    if not tokens:
        return ""
    quoted = [f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens]
    return " OR ".join(quoted)


def _intent_tokens(raw: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9_-]*", raw.lower()):
        if len(token) < 3 or token in _TODO_INTENT_STOPWORDS or token in tokens:
            continue
        tokens.append(token)
    return tokens[:6]


def _todo_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["name"],
        "priority": row.get("priority"),
        "domain": row.get("domain"),
        "context": row.get("context"),
        "workflow_state": row.get("workflow_state"),
        "description": row.get("description"),
        "source_uri": row.get("source_uri"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "edge_count": row.get("edge_count", 0),
    }


def _todo_filter_sql(
    *,
    workflow_state: str | None,
    priority: str | None,
    domain: str | None,
    domain_exclude: str | None,
    context: str | None,
) -> tuple[list[str], list[str]]:
    clauses = ["e.type = 'todo'"]
    params: list[str] = []

    if workflow_state is not None:
        clauses.append("e.workflow_state = ?")
        params.append(workflow_state)
    if priority:
        priorities = _csv_values(priority)
        if priorities:
            placeholders = ",".join("?" for _ in priorities)
            clauses.append(
                f"json_extract(e.attributes, '$.priority') IN ({placeholders})"
            )
            params.extend(priorities)
    if domain:
        domains = _csv_values(domain)
        if domains:
            placeholders = ",".join("?" for _ in domains)
            clauses.append(
                f"json_extract(e.attributes, '$.domain') IN ({placeholders})"
            )
            params.extend(domains)
    excluded_domains = _csv_values(domain_exclude)
    if excluded_domains:
        placeholders = ",".join("?" for _ in excluded_domains)
        clauses.append(
            f"(json_extract(e.attributes, '$.domain') IS NULL "
            f"OR json_extract(e.attributes, '$.domain') NOT IN ({placeholders}))"
        )
        params.extend(excluded_domains)
    if context:
        clauses.append("json_extract(e.attributes, '$.context') = ?")
        params.append(context)
    return clauses, params


def _todo_edge_join(conn: sqlite3.Connection) -> str:
    if not _table_exists(conn, "session_edges"):
        return "LEFT JOIN (SELECT NULL AS node, 0 AS edge_count WHERE 0) ec ON 0"
    return """
        LEFT JOIN (
            SELECT node, COUNT(*) AS edge_count
            FROM (
                SELECT from_node AS node FROM session_edges WHERE valid_until IS NULL
                UNION ALL
                SELECT to_node AS node FROM session_edges WHERE valid_until IS NULL
            )
            GROUP BY node
        ) ec ON ec.node = e.id
    """


def _query_todo_candidates(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    limit: int = 10,
    workflow_state: str | None = "open",
    priority: str | None = None,
    domain: str | None = None,
    domain_exclude: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """Rank TODOs using intent text, metadata, graph links, specs, and recency."""
    clauses, filter_params = _todo_filter_sql(
        workflow_state=workflow_state,
        priority=priority,
        domain=domain,
        domain_exclude=domain_exclude,
        context=context,
    )

    normalized_q = q.strip() if q else ""
    fts_query = _sanitize_fts_query(normalized_q)
    has_fts = bool(fts_query) and _table_exists(conn, "assertions_fts")
    cte = ""
    join_fts = ""
    cte_params: list[str] = []

    if has_fts:
        cte = """
            WITH fts_matches AS (
                SELECT a.entity_id, MIN(f.rank) AS fts_rank
                FROM assertions_fts f
                JOIN assertions a ON a.id = f.assertion_id
                JOIN entities te ON te.id = a.entity_id
                WHERE f.indexed_text MATCH ?
                  AND a.superseded_by IS NULL
                  AND te.type = 'todo'
                GROUP BY a.entity_id
            )
        """
        cte_params.append(fts_query)
        join_fts = "LEFT JOIN fts_matches fm ON fm.entity_id = e.id"

    intent_score = "0"
    score_params: list[str] = []
    intent_params: list[str] = []
    if normalized_q:
        like = f"%{normalized_q.lower()}%"
        match_clauses = [
            "lower(e.id) = lower(?) OR "
            "lower(e.id) LIKE ? OR "
            "lower(e.name) LIKE ? OR "
            "lower(coalesce(e.description, '')) LIKE ? OR "
            "lower(coalesce(e.source_uri, '')) LIKE ?"
        ]
        if has_fts:
            match_clauses.append("fm.entity_id IS NOT NULL")
        intent_params.extend([normalized_q, like, like, like, like])
        intent_score = (
            "CASE WHEN lower(e.id) = lower(?) THEN 140 ELSE 0 END + "
            "CASE WHEN lower(e.id) LIKE ? THEN 90 ELSE 0 END + "
            "CASE WHEN lower(e.name) LIKE ? THEN 80 ELSE 0 END + "
            "CASE WHEN lower(coalesce(e.description, '')) LIKE ? THEN 45 ELSE 0 END + "
            "CASE WHEN lower(coalesce(e.source_uri, '')) LIKE ? THEN 25 ELSE 0 END"
        )
        if has_fts:
            intent_score += " + CASE WHEN fm.entity_id IS NOT NULL THEN 60 ELSE 0 END"
        score_params.extend([normalized_q, like, like, like, like])
        for token in _intent_tokens(normalized_q):
            token_like = f"%{token}%"
            match_clauses.append(
                "("
                "lower(e.id) LIKE ? OR "
                "lower(e.name) LIKE ? OR "
                "lower(coalesce(e.description, '')) LIKE ? OR "
                "lower(coalesce(e.source_uri, '')) LIKE ?"
                ")"
            )
            intent_params.extend([token_like, token_like, token_like, token_like])
            intent_score += (
                " + CASE WHEN lower(e.id) LIKE ? THEN 25 ELSE 0 END"
                " + CASE WHEN lower(e.name) LIKE ? THEN 20 ELSE 0 END"
                " + CASE WHEN lower(coalesce(e.description, '')) LIKE ? THEN 8 ELSE 0 END"
                " + CASE WHEN lower(coalesce(e.source_uri, '')) LIKE ? THEN 5 ELSE 0 END"
            )
            score_params.extend([token_like, token_like, token_like, token_like])
        clauses.append(f"({' OR '.join(match_clauses)})")

    where = " AND ".join(clauses)
    edge_join = _todo_edge_join(conn)
    sql = f"""
        {cte}
        SELECT e.id, e.name, e.description, e.workflow_state, e.source_uri,
               e.created_at, e.updated_at,
               json_extract(e.attributes, '$.priority') AS priority,
               json_extract(e.attributes, '$.domain') AS domain,
               json_extract(e.attributes, '$.context') AS context,
               coalesce(ec.edge_count, 0) AS edge_count,
               (
                   {intent_score}
                   + CASE json_extract(e.attributes, '$.priority')
                       WHEN 'high' THEN 30
                       WHEN 'medium' THEN 15
                       ELSE 0
                     END
                   + CASE WHEN e.source_uri IS NOT NULL THEN 8 ELSE 0 END
                   + min(coalesce(ec.edge_count, 0), 5) * 4
                   + CASE
                       WHEN julianday('now') - julianday(e.updated_at) <= 7 THEN 12
                       WHEN julianday('now') - julianday(e.updated_at) <= 30 THEN 6
                       ELSE 0
                     END
               ) AS relevance_score
        FROM entities e
        {join_fts}
        {edge_join}
        WHERE {where}
        ORDER BY relevance_score DESC, e.updated_at DESC
        LIMIT ?
    """
    params = cte_params + score_params + filter_params + intent_params + [limit]
    rows = db_query(conn, sql, tuple(params))
    return {
        "query": normalized_q or None,
        "items": [
            {**_todo_row(row), "relevance_score": row["relevance_score"]}
            for row in rows
        ],
        "total": len(rows),
        "retrieval": "ranked_intent" if normalized_q else "ranked_boot",
    }


@router.get("/todo-candidates", openapi_extra=x_mcp("todo_candidates"))
def get_todo_candidates(
    q: str | None = Query(
        None,
        description="Intent text, e.g. 'execute on the TODO regarding Cortex retrieval'.",
    ),
    limit: int = Query(10, ge=1, le=50),
    workflow_state: str | None = Query("open"),
    priority: str | None = Query(None, description="Comma-separated priority filter."),
    domain: str | None = Query(None, description="Comma-separated domain filter."),
    domain_exclude: str | None = Query(None, description="Comma-separated exclusions."),
    context: str | None = Query(None),
) -> dict[str, Any]:
    """Small ranked TODO candidate set for agent/user intent retrieval."""
    with cortex_conn() as conn:
        return _query_todo_candidates(
            conn,
            q=q,
            limit=limit,
            workflow_state=workflow_state,
            priority=priority,
            domain=domain,
            domain_exclude=domain_exclude,
            context=context,
        )
