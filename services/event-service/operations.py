"""Named operations catalog — discoverable pre-built queries.

Operations are the primary agent API. Each operation is a named query with
typed parameters and structured results. Agents discover operations via
the 'operations' endpoint, then invoke by name.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, TypedDict

from .store import EventStore

logger = logging.getLogger(__name__)
_SESSION_BOUNDARY_SIGNAL = "system.started"


@dataclass(slots=True)
class OperationDef:
    """Describes a named operation for agent discovery."""

    name: str
    description: str
    params: dict[str, ParamDef]
    returns: str


class ParamDef(TypedDict, total=False):
    type: str
    # TypedDict cannot express "default type follows the string in `type`";
    # runtime coercion/validation is handled by operation implementations.
    default: str | int | float | bool | None
    required: bool


_OPERATIONS: dict[str, OperationDef] = {}


def _register(op: OperationDef) -> OperationDef:
    _OPERATIONS[op.name] = op
    return op


_register(
    OperationDef(
        name="recent-failures",
        description="Last N failure/error events (default window: since last Stargate restart)",
        params={
            "limit": {"type": "int", "default": 20},
            "since_ts": {"type": "int"},
        },
        returns="event rows",
    )
)

_register(
    OperationDef(
        name="noise-profile",
        description="Signal frequency histogram (default window: since last Stargate restart)",
        params={"minutes": {"type": "int"}},
        returns="signal counts",
    )
)

_register(
    OperationDef(
        name="coordination-audit",
        description="Recent role=coordination events (default window: since last Stargate restart)",
        params={
            "limit": {"type": "int", "default": 50},
            "since_ts": {"type": "int"},
        },
        returns="event rows",
    )
)

_register(
    OperationDef(
        name="model-timeline",
        description="Load/execute/unload events for a specific model (default window: since last Stargate restart)",
        params={
            "model_id": {"type": "string", "required": True},
            "since_ts": {"type": "int"},
        },
        returns="event rows",
    )
)

_register(
    OperationDef(
        name="request-trace",
        description="All events sharing a request_id",
        params={
            "request_id": {"type": "string", "required": True},
            "limit": {"type": "int", "default": 200},
        },
        returns="event rows",
    )
)

_register(
    OperationDef(
        name="request-lifecycle",
        description="Full snapshot phases for a request",
        params={
            "request_id": {"type": "string", "required": True},
            "limit": {"type": "int", "default": 200},
        },
        returns="snapshot rows",
    )
)

_register(
    OperationDef(
        name="request-summary",
        description="Aggregate request stats (default window: since last Stargate restart)",
        params={"minutes": {"type": "int"}},
        returns="summary object",
    )
)

_register(
    OperationDef(
        name="pipeline-trace",
        description="Step-by-step trace for a pipeline execution",
        params={
            "execution_id": {"type": "string", "required": True},
            "limit": {"type": "int", "default": 200},
        },
        returns="compiled trace",
    )
)

_register(
    OperationDef(
        name="compare-runs",
        description="Side-by-side metrics for two pipeline executions",
        params={
            "run_a": {"type": "string", "required": True},
            "run_b": {"type": "string", "required": True},
        },
        returns="diff object",
    )
)

_register(
    OperationDef(
        name="federation-health",
        description="Latest telemetry/connection per remote relay (default window: since last Stargate restart)",
        params={
            "limit": {"type": "int", "default": 50},
            "since_ts": {"type": "int"},
        },
        returns="health summary",
    )
)

_register(
    OperationDef(
        name="capacity-snapshot",
        description="Current slot usage from recent execution events (default window: since last Stargate restart)",
        params={
            "limit": {"type": "int", "default": 50},
            "since_ts": {"type": "int"},
        },
        returns="capacity summary",
    )
)

_register(
    OperationDef(
        name="signal-events",
        description="Recent events matching a signal pattern, with full payload",
        params={
            "signal": {"type": "string", "required": True},
            "limit": {"type": "int", "default": 20},
            "execution_id": {"type": "string"},
            "since_ts": {"type": "int"},
        },
        returns="event rows with payload",
    )
)

_register(
    OperationDef(
        name="realtime-snapshot",
        description="Last N events from the in-memory realtime ring buffer (no SQLite)",
        params={"limit": {"type": "int", "default": 100}},
        returns="realtime event rows",
    )
)

_STARTUP_SIGNALS: tuple[str, ...] = (
    "cloud.proxy.started",
    "event.service.started",
    "rag.started",
    "system.started",
)

_register(
    OperationDef(
        name="stack-last-started",
        description="Per-service last startup timestamp and overall session start",
        params={},
        returns="per-service rows + session boundary timestamp",
    )
)


def list_operations() -> list[dict[str, Any]]:
    """Return all operations as dicts for agent discovery."""
    return [asdict(op) for op in _OPERATIONS.values()]


def get_operation(name: str) -> OperationDef | None:
    return _OPERATIONS.get(name)


async def execute_operation(
    name: str,
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    """Execute a named operation against the store."""
    op = get_operation(name)
    if not op:
        return {
            "error": f"Unknown operation: {name}. Use 'operations' to list available.",
            "code": -32601,
        }

    try:
        return await _DISPATCH[name](params, store)
    except Exception as e:
        logger.exception("Operation %s failed with params=%s", name, params)
        return {"error": "Operation failed", "error_type": e.__class__.__name__}


async def _resolve_window_minutes_and_cutoff(
    params: dict[str, Any],
    store: EventStore,
    *,
    default_minutes: int = 5,
) -> tuple[int, int]:
    """Return `(minutes, cutoff_ts_ms)` using session-aware default semantics."""
    minutes = _coerce_minutes(params.get("minutes"))
    if minutes is None:
        session_start_ts = await _get_session_start_ts(store)
        if session_start_ts is not None:
            elapsed_ms = int(time.time() * 1000) - session_start_ts
            minutes = max(1, elapsed_ms // 60_000 + 1)
        else:
            minutes = default_minutes
    cutoff = int(time.time() * 1000) - (minutes * 60 * 1000)
    return minutes, cutoff


async def _recent_failures(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    limit = _coerce_limit(params.get("limit", 20))
    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        since_ts = await _get_session_start_ts(store)
    where = [
        "(signal LIKE '%.failed' OR signal LIKE '%.error')",
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


def _coerce_limit(value: Any, default: int = 20) -> int:
    """Coerce user limit to a bounded positive integer."""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, 500))


def _coerce_minutes(value: Any) -> int | None:
    """Coerce minutes to a positive integer; None means auto-window."""
    if value is None:
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(minutes, 24 * 60))


def _coerce_since_ts(value: Any) -> int | None:
    """Coerce optional since_ts to Unix milliseconds."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _get_session_start_ts(store: EventStore) -> int | None:
    """Return ts_unix_ms of the most recent Stargate session boundary."""
    rows = await store.query(
        "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = ?",
        (_SESSION_BOUNDARY_SIGNAL,),
        limit=1,
    )
    if rows and rows[0].get("ts") is not None:
        return int(rows[0]["ts"])
    return None


async def _signal_events(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    """Fetch recent events by exact signal or '*' glob, including parsed payload."""
    signal = params.get("signal") or ""
    if not signal:
        return {"error": "signal is required"}

    limit = _coerce_limit(params.get("limit", 20))
    execution_id = params.get("execution_id")
    since_ts = _coerce_since_ts(params.get("since_ts"))
    if since_ts is None:
        since_ts = await _get_session_start_ts(store)

    signal_clause = "signal LIKE ?" if "*" in signal else "signal = ?"
    signal_value = signal.replace("*", "%") if "*" in signal else signal

    sql = (
        "SELECT seq, signal, source, timestamp, execution_id, model_id, payload "
        f"FROM events WHERE {signal_clause}"
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
        (
            r.get("timestamp")
            for r in rows
            if r.get("signal") == _SESSION_BOUNDARY_SIGNAL
        ),
        None,
    )
    return {
        "rows": rows,
        "count": len(rows),
        "stack_start_ts_unix_ms": session_start_ts,
        "stack_start_timestamp": session_timestamp,
    }


OperationCallable = Callable[[dict[str, Any], EventStore], Awaitable[dict[str, Any]]]

_DISPATCH: dict[str, OperationCallable] = {
    "recent-failures": _recent_failures,
    "noise-profile": _noise_profile,
    "coordination-audit": _coordination_audit,
    "model-timeline": _model_timeline,
    "request-trace": _request_trace,
    "request-lifecycle": _request_lifecycle,
    "request-summary": _request_summary,
    "pipeline-trace": _pipeline_trace,
    "compare-runs": _compare_runs,
    "federation-health": _federation_health,
    "capacity-snapshot": _capacity_snapshot,
    "signal-events": _signal_events,
    "stack-last-started": _stack_last_started,
    "realtime-snapshot": _realtime_snapshot,
}
