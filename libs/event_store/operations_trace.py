"""Advanced trace and comparison operation implementations."""

from __future__ import annotations

import json
from typing import Any

from universal_logging import get_logger

from .dispatch_journal import fetch_dispatch_journal_summary
from .operation_parameters import (
    _coerce_limit,
    _coerce_since_ts,
    _get_session_start_ts,
    _resolve_window_minutes_and_cutoff,
)
from .store import EventStore

logger = get_logger(__name__)

_STARTUP_SIGNALS: tuple[str, ...] = (
    "cloud.proxy.started",
    "event.service.started",
    "rag.started",
    "system.started",
)


async def _event_store_floor(store: EventStore) -> dict[str, Any]:
    rows = await store.query(
        "SELECT seq, ts_unix_ms, timestamp FROM events ORDER BY ts_unix_ms ASC, seq ASC LIMIT 1",
        (),
        limit=1,
    )
    count_rows = await store.query("SELECT COUNT(*) AS count FROM events", (), limit=1)
    count = int(count_rows[0].get("count") or 0) if count_rows else 0
    if not rows:
        return {"event_count": count, "floor_ts_unix_ms": None, "floor_timestamp": None}
    row = rows[0]
    return {
        "event_count": count,
        "floor_ts_unix_ms": row.get("ts_unix_ms"),
        "floor_timestamp": row.get("timestamp"),
    }


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
    if not rows:
        retention = await _event_store_floor(store)
        cold_record = fetch_dispatch_journal_summary(execution_id)
        if cold_record is not None:
            return {
                "execution_id": execution_id,
                "steps": [],
                "event_count": 0,
                "limit": limit,
                "total_tokens_in": 0,
                "total_tokens_out": 0,
                "total_tokens": 0,
                "error": {
                    "code": "pipeline_trace_aged_out",
                    "message": (
                        "No warm events remain for this execution_id, but the "
                        "dispatch journal confirms the execution existed. "
                        "Reproduce with telemetry coverage for a full trace."
                    ),
                },
                "retention": retention,
                "cold_record": cold_record,
            }
        return {
            "execution_id": execution_id,
            "steps": [],
            "event_count": 0,
            "limit": limit,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_tokens": 0,
            "error": {
                "code": "pipeline_trace_not_found",
                "message": (
                    "No events or dispatch-journal record found for this execution_id. "
                    "If the execution predates the retention floor, the trace may have "
                    "aged out before investigation."
                ),
            },
            "retention": retention,
        }

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
        elif "model" in payload:
            step_info["model"] = payload["model"]
        if "model_entity_id" in payload:
            step_info["model_entity_id"] = payload["model_entity_id"]
        if "duration_ms" in payload:
            step_info["duration_ms"] = payload["duration_ms"]
        tokens_in = payload.get("tokens_in")
        if tokens_in is None:
            tokens_in = payload.get("prompt_tokens")
        if tokens_in is None:
            tokens_in = payload.get("input_tokens")
        if tokens_in is not None:
            tokens_in = int(tokens_in)
            step_info["tokens_in"] = tokens_in
            total_tokens_in += tokens_in
        tokens_out = payload.get("tokens_out")
        if tokens_out is None:
            tokens_out = payload.get("completion_tokens")
        if tokens_out is None:
            tokens_out = payload.get("output_tokens")
        if tokens_out is not None:
            tokens_out = int(tokens_out)
            step_info["tokens_out"] = tokens_out
            total_tokens_out += tokens_out
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


async def _provider_health(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    """Aggregate frontier dispatch health per provider across MCP + pipeline surfaces.

    Reads both the legacy ``mcp.frontier.*`` family (still emitted by
    ``_frontier_core.execute_frontier`` for direct MCP ``frontier_dispatch``
    callers) and the hoisted ``pipeline.frontier.dispatch.*`` family emitted
    by ``frontier_dispatch_v1``. Bucket shape is provider-symmetric so
    Phase 2+ collapse of the MCP surface onto the pipeline leaves the
    response envelope unchanged.
    """
    minutes, cutoff = await _resolve_window_minutes_and_cutoff(params, store)
    provider_filter = params.get("provider") or None

    rows = await store.query(
        "SELECT signal, payload FROM events "
        "WHERE (signal LIKE 'mcp.frontier.%' "
        "OR signal LIKE 'pipeline.frontier.dispatch.%') "
        "AND role NOT IN ('debug', 'realtime') "
        "AND ts_unix_ms >= ? "
        "ORDER BY seq DESC",
        (cutoff,),
        limit=5000,
    )

    providers: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw = row.get("payload")
        payload: dict[str, Any] = {}
        if isinstance(raw, str) and raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
        prov = payload.get("provider") or "unknown"
        if provider_filter and prov != provider_filter:
            continue
        bucket = providers.setdefault(
            prov,
            {
                "called": 0,
                "completed": 0,
                "errors": {},
                "output_short": 0,
                "tool_executed": 0,
                "_total_output_tokens": 0,
                "_total_duration_s": 0.0,
                "_duration_samples": 0,
            },
        )
        sig = row["signal"]
        if sig in (
            "mcp.frontier.generate.called",
            "pipeline.frontier.dispatch.started",
        ):
            bucket["called"] += 1
        elif sig == "mcp.frontier.generate.completed":
            bucket["completed"] += 1
            bucket["_total_output_tokens"] += int(payload.get("output_tokens") or 0)
            duration = payload.get("duration_s")
            if isinstance(duration, int | float):
                bucket["_total_duration_s"] += float(duration)
                bucket["_duration_samples"] += 1
        elif sig in (
            "pipeline.frontier.dispatch.completed",
            "pipeline.frontier.dispatch.exhausted",
        ):
            bucket["completed"] += 1
            bucket["_total_output_tokens"] += int(payload.get("completion_tokens") or 0)
        elif sig == "mcp.frontier.generate.error":
            err = payload.get("error") or "unknown"
            bucket["errors"][err] = bucket["errors"].get(err, 0) + 1
        elif sig == "pipeline.frontier.dispatch.remotemcp.misconfigured":
            bucket["errors"]["remotemcp_misconfigured"] = (
                bucket["errors"].get("remotemcp_misconfigured", 0) + 1
            )
        elif sig in (
            "mcp.frontier.output.short",
            "pipeline.frontier.dispatch.output.short",
        ):
            bucket["output_short"] += 1
        elif sig in (
            "mcp.frontier.tool.executed",
            "pipeline.frontier.dispatch.tool.called",
            "pipeline.frontier.dispatch.tool.failed",
        ):
            bucket["tool_executed"] += 1

    for prov, b in providers.items():
        completed = b["completed"] or 0
        samples = b.pop("_duration_samples") or 0
        total_dur = b.pop("_total_duration_s")
        total_out = b.pop("_total_output_tokens")
        b["error_count"] = sum(b["errors"].values())
        b["avg_output_tokens"] = (total_out // completed) if completed else 0
        b["avg_duration_s"] = round(total_dur / samples, 3) if samples else 0.0
        b["short_rate"] = round(b["output_short"] / completed, 3) if completed else 0.0

    return {
        "window_minutes": minutes,
        "provider_filter": provider_filter,
        "providers": providers,
        "provider_count": len(providers),
    }


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
