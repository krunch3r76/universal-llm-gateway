"""Advanced trace and comparison operation implementations."""

from __future__ import annotations

import json
import logging
from typing import Any

from .operations import _coerce_limit, _coerce_since_ts, _get_session_start_ts
from .store import EventStore

logger = logging.getLogger(__name__)

_STARTUP_SIGNALS: tuple[str, ...] = (
    "cloud.proxy.started",
    "event.service.started",
    "rag.started",
    "system.started",
)


async def _pipeline_trace(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    execution_id = params.get("execution_id") or ""
    if not execution_id:
        return {"error": "execution_id is required"}
    limit = _coerce_limit(params.get("limit", 200), default=200)

    rows = await store.query(
        "SELECT * FROM events WHERE execution_id = ? ORDER BY seq DESC LIMIT ?",
        (execution_id, limit),
    )
    rows.reverse()

    steps: list[dict[str, Any]] = []
    total_tokens_in = 0
    total_tokens_out = 0

    for row in rows:
        payload: dict[str, Any] = {}
        raw_payload = row.get("payload")
        if isinstance(raw_payload, str) and raw_payload:
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to decode pipeline payload JSON for seq=%s: %s",
                    row.get("seq"),
                    e,
                )
                payload = {}

        step_info: dict[str, Any] = {
            "signal": row["signal"],
            "source": row["source"],
            "ts": row["timestamp"],
        }
        if "model_id" in payload:
            step_info["model"] = payload["model_id"]
        if "duration_ms" in payload:
            step_info["duration_ms"] = payload["duration_ms"]
        if "tokens_in" in payload:
            step_info["tokens_in"] = payload["tokens_in"]
            total_tokens_in += payload["tokens_in"]
        if "tokens_out" in payload:
            step_info["tokens_out"] = payload["tokens_out"]
            total_tokens_out += payload["tokens_out"]
        if "status" in payload:
            step_info["status"] = payload["status"]

        steps.append(step_info)

    total_tokens = total_tokens_in + total_tokens_out
    return {
        "execution_id": execution_id,
        "steps": steps,
        "event_count": len(rows),
        "limit": limit,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_tokens": total_tokens,
    }


async def _compare_runs(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    run_a = params.get("run_a") or ""
    run_b = params.get("run_b") or ""
    if not run_a or not run_b:
        return {"error": "run_a and run_b are required"}

    trace_a = await _pipeline_trace({"execution_id": run_a}, store)
    trace_b = await _pipeline_trace({"execution_id": run_b}, store)

    return {
        "run_a": trace_a,
        "run_b": trace_b,
        "diff": {
            "event_count_delta": trace_b.get("event_count", 0)
            - trace_a.get("event_count", 0),
            "token_delta": trace_b.get("total_tokens", 0)
            - trace_a.get("total_tokens", 0),
        },
    }


async def _federation_health(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    limit = _coerce_limit(params.get("limit", 50), default=50)
    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        since_ts = await _get_session_start_ts(store)
    sql = "SELECT * FROM events WHERE signal LIKE 'federation.%' AND role NOT IN ('debug', 'realtime')"
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


async def _capacity_snapshot(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    limit = _coerce_limit(params.get("limit", 50), default=50)
    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        since_ts = await _get_session_start_ts(store)
    sql = (
        "SELECT * FROM events "
        "WHERE signal IN ('model.execution.completed', 'model.execution.failed', "
        "'model.capacity.freed', 'model.loaded', 'model.unloaded') "
        "AND role NOT IN ('debug', 'realtime')"
    )
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


async def _realtime_snapshot(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    """Return events from the in-memory realtime ring buffer."""
    limit = _coerce_limit(params.get("limit", 100), default=100)
    rows = store.get_realtime_snapshot(limit)
    return {"rows": rows, "count": len(rows), "buffer_source": "memory"}


async def _verify_tool_execution(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    """Return the most recent completed/failed event matching a signal prefix.

    Agents use this to verify that a claimed MCP tool call actually executed.
    The prefix is typically the tool's event namespace (e.g. "mcp.rag.pipeline").
    """
    signal_prefix = params.get("signal_prefix") or ""
    if not signal_prefix:
        return {"error": "signal_prefix is required"}

    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        since_ts = await _get_session_start_ts(store)

    sql = (
        "SELECT seq, signal, source, timestamp, execution_id, model_id, payload "
        "FROM events "
        "WHERE signal LIKE ? AND signal NOT LIKE '%.called' "
        "AND role NOT IN ('debug', 'realtime')"
    )
    query_params: list[Any] = [f"{signal_prefix}%"]
    if since_ts is not None:
        sql += " AND ts_unix_ms >= ?"
        query_params.append(since_ts)
    sql += " ORDER BY seq DESC LIMIT 1"

    rows = await store.query(sql, tuple(query_params))
    if not rows:
        return {"verified": False, "signal_prefix": signal_prefix, "event": None}

    row_dict = dict(rows[0])
    payload = row_dict.get("payload")
    if isinstance(payload, str):
        try:
            row_dict["payload"] = json.loads(payload)
        except json.JSONDecodeError:
            pass
    return {"verified": True, "signal_prefix": signal_prefix, "event": row_dict}


async def _stack_last_started(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    """Per-service last startup timestamps + overall session boundary."""
    placeholders = ", ".join("?" for _ in _STARTUP_SIGNALS)
    rows = await store.query(
        "SELECT signal, ts_unix_ms, timestamp FROM ("
        "  SELECT signal, ts_unix_ms, timestamp, seq, "
        "         ROW_NUMBER() OVER ("
        "           PARTITION BY signal "
        "           ORDER BY ts_unix_ms DESC, seq DESC"
        "         ) AS rn "
        "  FROM events "
        f"  WHERE signal IN ({placeholders})"
        ") ranked WHERE rn = 1 ORDER BY ts_unix_ms DESC, signal ASC",
        tuple(_STARTUP_SIGNALS),
        limit=len(_STARTUP_SIGNALS),
    )
    session_start_ts = await _get_session_start_ts(store)
    session_timestamp = next(
        (r.get("timestamp") for r in rows if r.get("signal") == "system.started"),
        None,
    )
    return {
        "rows": rows,
        "count": len(rows),
        "stack_start_ts_unix_ms": session_start_ts,
        "stack_start_timestamp": session_timestamp,
    }
