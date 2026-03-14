"""HTTP query handler — operations, structured filter, raw SQL.

Three-tier API:
  1. Named operations (primary agent API) — discoverable, typed
  2. Structured filter — safe ad-hoc queries
  3. Raw SQL — restricted escape hatch for debugging

Served over UDS at /tmp/universal-protocol/events-query.sock via aiohttp.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

from .operations import execute_operation, list_operations
from .store import EventStore

logger = logging.getLogger(__name__)

_MAX_QUERY_ROWS = 1000
_ALLOWED_SQL_PREFIXES = ("SELECT", "EXPLAIN")


async def query_handler(request: web.Request) -> web.Response:
    """Handle POST /v1/query — operations, filters, or raw SQL."""
    store: EventStore = request.app["store"]

    try:
        data_raw: object = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(data_raw, dict):
        return web.json_response(
            {"error": "Request body must be a JSON object"}, status=400
        )
    data: dict[str, Any] = data_raw

    query_type = data.get("type", "")
    if not isinstance(query_type, str):
        return web.json_response({"error": "Field 'type' must be a string"}, status=400)

    if query_type == "operations":
        return web.json_response(
            {
                "type": "operations",
                "operations": list_operations(),
            }
        )

    if query_type == "operation":
        name = data.get("name", "")
        params = data.get("params", {})
        if not isinstance(name, str) or not name.strip():
            return web.json_response(
                {"error": "Field 'name' must be a non-empty string"},
                status=400,
            )
        if not isinstance(params, dict):
            return web.json_response(
                {"error": "Field 'params' must be an object"},
                status=400,
            )
        result = await execute_operation(name, params, store)
        return web.json_response({"type": "result", "operation": name, **result})

    if query_type == "query":
        return await _structured_query(data, store)

    if query_type == "sql":
        return await _raw_sql(data, store)

    return web.json_response(
        {
            "error": f"Unknown query type: {query_type}. Use: operations, operation, query, sql"
        },
        status=400,
    )


async def health_handler(request: web.Request) -> web.Response:
    """GET /health — basic liveness check."""
    ingest = request.app.get("ingest")
    metrics = ingest.get_metrics() if ingest else {}
    subs = len(request.app.get("subscriber_queues", set()))
    return web.json_response(
        {
            "status": "ok",
            "subscribers": subs,
            **metrics,
        }
    )


async def metrics_handler(request: web.Request) -> web.Response:
    """GET /metrics — internal counters for monitoring."""
    ingest = request.app.get("ingest")
    metrics = ingest.get_metrics() if ingest else {}
    subs = len(request.app.get("subscriber_queues", set()))
    return web.json_response(
        {
            "active_subscribers": subs,
            **metrics,
        }
    )


async def _structured_query(data: dict[str, Any], store: EventStore) -> web.Response:
    """Handle structured filter queries."""
    filt = data.get("filter", {})
    if not isinstance(filt, dict):
        return web.json_response(
            {"error": "Field 'filter' must be an object"}, status=400
        )
    limit_raw = data.get("limit", 100)
    if not isinstance(limit_raw, int):
        return web.json_response(
            {"error": "Field 'limit' must be an integer"}, status=400
        )
    limit = min(limit_raw, _MAX_QUERY_ROWS)
    since = data.get("since")
    if since is not None and not isinstance(since, str):
        return web.json_response(
            {"error": "Field 'since' must be an ISO-8601 string"},
            status=400,
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
            return web.json_response(
                {"error": "Invalid 'since' timestamp format. Expected ISO 8601."},
                status=400,
            )

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM events WHERE {where} ORDER BY seq DESC LIMIT ?"
    params.append(limit)

    rows = await store.query(sql, tuple(params), limit=limit)
    return web.json_response({"type": "result", "rows": rows, "count": len(rows)})


async def _raw_sql(data: dict[str, Any], store: EventStore) -> web.Response:
    """Handle raw SQL queries (restricted to SELECT/EXPLAIN).

    Accepts optional ``params`` list for parameterized queries::

        {"type": "sql", "sql": "SELECT * FROM events WHERE execution_id = ?", "params": ["abc123"]}
    """
    sql = data.get("sql", "").strip()
    if not sql:
        return web.json_response({"error": "Empty SQL"}, status=400)

    upper = sql.upper().lstrip()
    if not any(upper.startswith(p) for p in _ALLOWED_SQL_PREFIXES):
        return web.json_response(
            {"error": "Only SELECT and EXPLAIN queries are allowed"},
            status=403,
        )

    raw_params = data.get("params", [])
    if not isinstance(raw_params, list):
        return web.json_response(
            {"error": "Field 'params' must be a list of bind values"},
            status=400,
        )

    limit = min(data.get("limit", 100), _MAX_QUERY_ROWS)
    rows = await store.query(sql, tuple(raw_params), limit=limit)
    return web.json_response({"type": "result", "rows": rows, "count": len(rows)})
