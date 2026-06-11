"""Core named query operation implementations."""

from __future__ import annotations

import json
import time
from typing import Any

from universal_logging import get_logger

from .operation_parameters import (
    _coerce_limit,
    _coerce_minutes,
    _coerce_since_ts,
    _get_session_start_ts,
    _resolve_window_minutes_and_cutoff,
    _signal_match_sql,
)
from .store import EventStore

logger = get_logger(__name__)


async def _recent_failures(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    limit = _coerce_limit(params.get("limit", 20))
    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        since_ts = await _get_session_start_ts(store)
    where = [
        "("
        "signal LIKE '%.failed' "
        "OR signal LIKE '%.error' "
        "OR signal LIKE '%.timeout' "
        "OR signal = 'fs.timeout.suspected'"
        ")",
        "role NOT IN ('debug', 'realtime')",
    ]
    query_params: list[Any] = []
    if since_ts is not None:
        where.append("ts_unix_ms >= ?")
        query_params.append(since_ts)
    query_params.append(limit)
    rows = await store.query(
        "SELECT * FROM events WHERE "
        + " AND ".join(where)
        + " ORDER BY seq DESC LIMIT ?",
        tuple(query_params),
    )
    return {"rows": rows, "count": len(rows)}


async def _noise_profile(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    minutes, cutoff = await _resolve_window_minutes_and_cutoff(params, store)
    rows = await store.query(
        "SELECT signal, COUNT(*) as count FROM events "
        "WHERE ts_unix_ms > ? AND role NOT IN ('debug', 'realtime') "
        "GROUP BY signal ORDER BY count DESC",
        (cutoff,),
    )
    return {"signals": rows, "minutes": minutes}


async def _coordination_audit(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    limit = _coerce_limit(params.get("limit", 50), default=50)
    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        since_ts = await _get_session_start_ts(store)
    sql = "SELECT * FROM events WHERE role = 'coordination'"
    query_params: list[Any] = []
    if since_ts is not None:
        sql += " AND ts_unix_ms >= ?"
        query_params.append(since_ts)
    sql += " ORDER BY seq DESC LIMIT ?"
    query_params.append(limit)
    rows = await store.query(
        sql,
        tuple(query_params),
    )
    return {"rows": rows, "count": len(rows)}


async def _model_timeline(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    model_id = params.get("model_id")
    if not model_id:
        return {"error": "model_id is required"}
    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        since_ts = await _get_session_start_ts(store)
    sql = "SELECT * FROM events WHERE model_id = ?"
    query_params: list[Any] = [model_id]
    if since_ts is not None:
        sql += " AND ts_unix_ms >= ?"
        query_params.append(since_ts)
    sql += " ORDER BY seq"
    rows = await store.query(sql, tuple(query_params))
    return {"rows": rows, "count": len(rows), "model_id": model_id}


async def _request_trace(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    request_id = params.get("request_id")
    if not request_id:
        return {"error": "request_id is required"}
    limit = _coerce_limit(params.get("limit", 200), default=200)
    rows = await store.query(
        "SELECT * FROM events WHERE request_id = ? ORDER BY seq DESC LIMIT ?",
        (request_id, limit),
    )
    rows.reverse()
    return {"rows": rows, "count": len(rows), "request_id": request_id, "limit": limit}


async def _request_lifecycle(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    request_id = params.get("request_id")
    if not request_id:
        return {"error": "request_id is required"}
    limit = _coerce_limit(params.get("limit", 200), default=200)
    rows = await store.query(
        "SELECT * FROM request_snapshots WHERE request_id = ? ORDER BY seq DESC LIMIT ?",
        (request_id, limit),
    )
    rows.reverse()
    return {"rows": rows, "count": len(rows), "request_id": request_id, "limit": limit}


async def _request_summary(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    minutes, cutoff = await _resolve_window_minutes_and_cutoff(params, store)
    rows = await store.query(
        "SELECT phase, COUNT(*) as count FROM request_snapshots "
        "WHERE ts_unix_ms > ? GROUP BY phase",
        (cutoff,),
    )
    return {"phases": rows, "minutes": minutes}


async def _signal_events(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    """Fetch recent events by exact signal or glob (* or % wildcards)."""
    signal = params.get("signal") or ""
    if not signal:
        return {"error": "signal is required"}

    limit = _coerce_limit(params.get("limit", 20))
    execution_id = params.get("execution_id")
    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        minutes = _coerce_minutes(params.get("minutes"))
        if minutes is not None:
            since_ts = int(time.time() * 1000) - (minutes * 60 * 1000)
        else:
            since_ts = await _get_session_start_ts(store)

    signal_predicate, signal_value = _signal_match_sql(signal)

    sql = (
        "SELECT seq, signal, source, timestamp, execution_id, model_id, payload "
        f"FROM events WHERE signal {signal_predicate}"
    )
    query_params: list[Any] = [signal_value]
    if execution_id:
        sql += " AND execution_id = ?"
        query_params.append(execution_id)
    if since_ts is not None:
        sql += " AND ts_unix_ms >= ?"
        query_params.append(since_ts)
    sql += " ORDER BY seq DESC LIMIT ?"
    query_params.append(limit)

    rows = await store.query(sql, tuple(query_params))
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        payload = row_dict.get("payload")
        if isinstance(payload, str):
            try:
                row_dict["payload"] = json.loads(payload)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to decode payload JSON for seq=%s: %s",
                    row_dict.get("seq"),
                    e,
                )
                row_dict["payload"] = payload
        out_rows.append(row_dict)

    return {"rows": out_rows, "count": len(out_rows)}
