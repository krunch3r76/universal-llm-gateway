"""HTTP query handler - operations, structured filter, raw SQL.

Three-tier API:
  1. Named operations (primary agent API) - discoverable, typed
  2. Structured filter - safe ad-hoc queries
  3. Raw SQL - restricted escape hatch for debugging

Served over UDS + optional TCP via FastAPI/uvicorn.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ingest import IngestServer
from .operations import execute_operation, list_operations
from .store import EventStore

logger = logging.getLogger(__name__)

_MAX_QUERY_ROWS = 1000
_ALLOWED_SQL_PREFIXES = ("SELECT", "EXPLAIN")


def create_query_router(
    store: EventStore,
    ingest: IngestServer,
    subscriber_queues: set[Any],
) -> APIRouter:
    """Build a FastAPI router with store/ingest injected via closure."""
    router = APIRouter()

    @router.post("/v1/query")
    async def query_handler(request: Request) -> JSONResponse:
        try:
            data: dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse(
                {"error": "Request body must be a JSON object"}, status_code=400
            )

        query_type = data.get("type", "")
        if not isinstance(query_type, str):
            return JSONResponse(
                {"error": "Field 'type' must be a string"}, status_code=400
            )

        if query_type == "operations":
            return JSONResponse({"type": "operations", "operations": list_operations()})

        if query_type == "operation":
            name = data.get("name", "")
            params = data.get("params", {})
            if not isinstance(name, str) or not name.strip():
                return JSONResponse(
                    {"error": "Field 'name' must be a non-empty string"},
                    status_code=400,
                )
            if not isinstance(params, dict):
                return JSONResponse(
                    {"error": "Field 'params' must be an object"},
                    status_code=400,
                )
            result = await execute_operation(name, params, store)
            return JSONResponse({"type": "result", "operation": name, **result})

        if query_type == "query":
            return await _structured_query(data, store)

        if query_type == "sql":
            return await _raw_sql(data, store)

        return JSONResponse(
            {
                "error": f"Unknown query type: {query_type}. Use: operations, operation, query, sql"
            },
            status_code=400,
        )

    @router.get("/health")
    async def health_handler() -> JSONResponse:
        metrics = ingest.get_metrics() if ingest else {}
        subs = len(subscriber_queues)
        return JSONResponse({"status": "ok", "subscribers": subs, **metrics})

    @router.get("/metrics")
    async def metrics_handler() -> JSONResponse:
        metrics = ingest.get_metrics() if ingest else {}
        subs = len(subscriber_queues)
        return JSONResponse({"active_subscribers": subs, **metrics})

    return router


async def _structured_query(data: dict[str, Any], store: EventStore) -> JSONResponse:
    """Handle structured filter queries."""
    filt = data.get("filter", {})
    if not isinstance(filt, dict):
        return JSONResponse(
            {"error": "Field 'filter' must be an object"}, status_code=400
        )
    limit_raw = data.get("limit", 100)
    if not isinstance(limit_raw, int):
        return JSONResponse(
            {"error": "Field 'limit' must be an integer"}, status_code=400
        )
    limit = min(limit_raw, _MAX_QUERY_ROWS)
    since = data.get("since")
    if since is not None and not isinstance(since, str):
        return JSONResponse(
            {"error": "Field 'since' must be an ISO-8601 string"},
            status_code=400,
        )

    conditions: list[str] = []
    params: list[Any] = []

    if "signal" in filt:
        pattern = filt["signal"]
        if "*" in pattern:
            conditions.append("signal LIKE ?")
            params.append(pattern.replace("*", "%"))
        else:
            conditions.append("signal = ?")
            params.append(pattern)

    if "scope" in filt:
        conditions.append("scope = ?")
        params.append(filt["scope"])

    if "role" in filt:
        conditions.append("role = ?")
        params.append(filt["role"])

    if "source" in filt:
        conditions.append("source = ?")
        params.append(filt["source"])

    if "request_id" in filt:
        conditions.append("request_id = ?")
        params.append(filt["request_id"])

    if "execution_id" in filt:
        conditions.append("execution_id = ?")
        params.append(filt["execution_id"])

    if since:
        from .store import _ts_ms_from_iso

        try:
            params.append(_ts_ms_from_iso(since))
            conditions.append("ts_unix_ms > ?")
        except ValueError:
            return JSONResponse(
                {"error": "Invalid 'since' timestamp format. Expected ISO 8601."},
                status_code=400,
            )

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM events WHERE {where} ORDER BY seq DESC LIMIT ?"
    params.append(limit)

    rows = await store.query(sql, tuple(params), limit=limit)
    return JSONResponse({"type": "result", "rows": rows, "count": len(rows)})


async def _raw_sql(data: dict[str, Any], store: EventStore) -> JSONResponse:
    """Handle raw SQL queries (restricted to SELECT/EXPLAIN).

    Accepts optional ``params`` list for parameterized queries::

        {"type": "sql", "sql": "SELECT ... WHERE execution_id = ?", "params": ["abc123"]}
    """
    sql = data.get("sql", "").strip()
    if not sql:
        return JSONResponse({"error": "Empty SQL"}, status_code=400)

    upper = sql.upper().lstrip()
    if not any(upper.startswith(p) for p in _ALLOWED_SQL_PREFIXES):
        return JSONResponse(
            {"error": "Only SELECT and EXPLAIN queries are allowed"},
            status_code=403,
        )

    raw_params = data.get("params", [])
    if not isinstance(raw_params, list):
        return JSONResponse(
            {"error": "Field 'params' must be a list of bind values"},
            status_code=400,
        )

    limit = min(data.get("limit", 100), _MAX_QUERY_ROWS)
    try:
        rows = await store.query(
            sql, tuple(raw_params), limit=limit, raise_on_error=True
        )
    except sqlite3.Error as e:
        # Surface malformed SQL (bad column, syntax error, etc.) as a 400
        # instead of silently returning []. The escape hatch is only useful
        # if failures are visible; silent-empty looks like "no data" and
        # causes agents to chase ghosts. Columns of `events`: seq, event_id,
        # signal, role, scope, ts_unix_ms, timestamp, source, request_id,
        # execution_id, model_id, gateway_id, payload.
        return JSONResponse(
            {"error": f"SQL error: {e}", "sql": sql[:200]},
            status_code=400,
        )
    return JSONResponse({"type": "result", "rows": rows, "count": len(rows)})
