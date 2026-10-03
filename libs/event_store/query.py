"""HTTP resource namespace for Event Service observability.

GET /api/v1/observability and /api/v1/observability/<member>, plus POST sql.
Served over UDS + optional TCP via FastAPI/uvicorn.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any

from deploy_identity.code_version import resolve_code_version
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .errors import EventStoreBusyError
from .ingest import IngestServer
from .operation_admission import admit
from .operation_catalog import get_operation, list_operations
from .operations import execute_operation
from .query_client_errors import ERROR_CLASS_LOCK_WAIT
from .query_path_health import record_query_completed
from .query_path_health import snapshot as query_path_snapshot
from .store import EventStore

logger = logging.getLogger(__name__)

_MAX_QUERY_ROWS = 1000


def _stamp_query_finished(started: float) -> None:
    record_query_completed(time.perf_counter() - started)
_ALLOWED_SQL_PREFIXES = ("SELECT", "EXPLAIN")


def create_query_router(
    store: EventStore,
    ingest: IngestServer,
    subscriber_queues: set[Any],
) -> APIRouter:
    """Build a FastAPI router with store/ingest injected via closure."""
    router = APIRouter()

    @router.get("/api/v1/observability")
    async def list_members() -> JSONResponse:
        members = []
        for row in list_operations():
            members.append(
                {
                    "name": row["name"],
                    "description": row["description"],
                    "params": row["params"],
                    "returns": row["returns"],
                    "method": row.get("method", "GET"),
                }
            )
        return JSONResponse({"category": "observability", "members": members})

    @router.api_route(
        "/api/v1/observability/{member}",
        methods=["GET", "POST"],
    )
    async def member_handler(member: str, request: Request) -> JSONResponse:
        names = {row["name"] for row in list_operations()}
        if member not in names:
            return _envelope(
                404,
                "UNKNOWN_MEMBER",
                f"Unknown member: {member}",
                data={"members": sorted(names)},
            )
        method = "POST" if member == "sql" else (
            get_operation(member).method if get_operation(member) else "GET"
        )
        if request.method != method:
            return _envelope(
                405,
                "METHOD_NOT_ALLOWED",
                f"{request.method} not allowed for {member}",
                data={"allow": method},
            )
        if member == "sql":
            return await _raw_sql(request, store)
        raw = dict(request.query_params)
        failure = admit(member, raw)
        if failure is not None:
            return JSONResponse(failure, status_code=400)
        params = _coerce_admitted(member, raw)
        started = time.perf_counter()
        try:
            result = await execute_operation(member, params, store)
        finally:
            _stamp_query_finished(started)
        return _handler_response(member, result)

    @router.get("/health")
    async def health_handler() -> JSONResponse:
        """Liveness + code identity + process pid for process_live settlement."""
        metrics = ingest.get_metrics() if ingest else {}
        subs = len(subscriber_queues)
        return JSONResponse(
            {
                "status": "ok",
                "subscribers": subs,
                **metrics,
                "query_path": query_path_snapshot(),
                "code_version": resolve_code_version(),
                "pid": os.getpid(),
            }
        )

    @router.get("/metrics")
    async def metrics_handler() -> JSONResponse:
        metrics = ingest.get_metrics() if ingest else {}
        subs = len(subscriber_queues)
        return JSONResponse({"active_subscribers": subs, **metrics})

    return router


def _envelope(
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    data: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {
            "code": code,
            "message": message,
            "source": "rpc",
            "retryable": retryable,
            "data": data or {},
        },
        status_code=status,
    )


def _coerce_admitted(member: str, raw: dict[str, str]) -> dict[str, Any]:
    op = get_operation(member)
    declared = dict(op.params) if op else {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        kind = declared.get(key, {}).get("type", "string")
        if kind == "int":
            out[key] = int(value, 10)
        else:
            out[key] = value
    return out


def _handler_response(member: str, result: dict[str, Any]) -> JSONResponse:
    if result.get("error_class") == ERROR_CLASS_LOCK_WAIT:
        return _envelope(
            503,
            "LOCK_WAIT",
            str(result.get("error") or "Event store waited on a database lock."),
            retryable=True,
            data={"error_class": ERROR_CLASS_LOCK_WAIT},
        )
    if (
        isinstance(result.get("error"), str)
        and not result.get("error_type")
        and not result.get("error_class")
    ):
        return _envelope(
            422,
            "OPERATION_REJECTED",
            str(result["error"]),
            data={"member": member},
        )
    if result.get("error_type"):
        return _envelope(
            500,
            "OPERATION_FAILED",
            str(result.get("error") or "Operation failed"),
            data={"error_type": result["error_type"]},
        )
    return JSONResponse({"operation": member, **result})


async def _raw_sql(request: Request, store: EventStore) -> JSONResponse:
    """POST body ``{"sql", "params"?, "limit"?}``. SELECT/EXPLAIN only."""
    try:
        data = await request.json()
    except Exception:
        return _envelope(400, "INVALID_PARAMS", "Invalid JSON", data={"member": "sql"})
    if not isinstance(data, dict):
        return _envelope(
            400, "INVALID_PARAMS", "Request body must be an object", data={"member": "sql"}
        )
    failure = admit("sql", data)
    if failure is not None:
        return JSONResponse(failure, status_code=400)
    sql = str(data.get("sql", "")).strip()
    if not sql:
        return _envelope(400, "INVALID_PARAMS", "Empty SQL", data={"member": "sql"})
    upper = sql.upper().lstrip()
    if not any(upper.startswith(prefix) for prefix in _ALLOWED_SQL_PREFIXES):
        return _envelope(
            403,
            "SQL_FORBIDDEN",
            "Only SELECT and EXPLAIN queries are allowed",
        )
    raw_params = data.get("params", [])
    limit_raw = data.get("limit", 100)
    limit = min(int(limit_raw), _MAX_QUERY_ROWS)
    started = time.perf_counter()
    try:
        try:
            rows = await store.query(
                sql, tuple(raw_params), limit=limit, raise_on_error=True
            )
        except EventStoreBusyError as exc:
            return _envelope(
                503,
                "LOCK_WAIT",
                f"Event store waited on a database lock: {exc}",
                retryable=True,
                data={"error_class": ERROR_CLASS_LOCK_WAIT},
            )
        except sqlite3.Error as exc:
            return _envelope(
                400,
                "SQL_ERROR",
                f"SQL error: {exc}",
                data={"sql": sql[:200]},
            )
        return JSONResponse({"rows": rows, "count": len(rows)})
    finally:
        _stamp_query_finished(started)
