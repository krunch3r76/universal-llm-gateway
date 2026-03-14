"""Named operations catalog — discoverable pre-built queries.

Operations are the primary agent API. Each operation is a named query with
typed parameters and structured results. Agents discover operations via
the 'operations' endpoint, then invoke by name.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OperationDef:
    """Describes a named operation for agent discovery."""

    name: str
    description: str
    params: dict[str, ParamDef]

class ParamDef(TypedDict, total=False):
    type: str
    default: Any
    required: bool
    returns: str


_OPERATIONS: dict[str, OperationDef] = {}


def _register(op: OperationDef) -> OperationDef:
    _OPERATIONS[op.name] = op
    return op


_register(
    OperationDef(
        name="recent-failures",
        description="Last N events matching *.failed or *.error signals",
        params={"limit": {"type": "int", "default": 20}},
        returns="event rows",
    )
)

_register(
    OperationDef(
        name="noise-profile",
        description="Signal frequency histogram for the last N minutes",
        params={"minutes": {"type": "int", "default": 5}},
        returns="signal counts",
    )
)

_register(
    OperationDef(
        name="coordination-audit",
        description="Recent role=coordination events",
        params={"limit": {"type": "int", "default": 50}},
        returns="event rows",
    )
)

_register(
    OperationDef(
        name="model-timeline",
        description="Load/execute/unload events for a specific model",
        params={"model_id": {"type": "string", "required": True}},
        returns="event rows",
    )
)

_register(
    OperationDef(
        name="request-trace",
        description="All events sharing a request_id",
        params={"request_id": {"type": "string", "required": True}},
        returns="event rows",
    )
)

_register(
    OperationDef(
        name="request-lifecycle",
        description="Full snapshot phases for a request",
        params={"request_id": {"type": "string", "required": True}},
        returns="snapshot rows",
    )
)

_register(
    OperationDef(
        name="request-summary",
        description="Aggregate request stats (count, avg latency, error rate)",
        params={"minutes": {"type": "int", "default": 5}},
        returns="summary object",
    )
)

_register(
    OperationDef(
        name="pipeline-trace",
        description="Step-by-step trace for a pipeline execution",
        params={"execution_id": {"type": "string", "required": True}},
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
        description="Latest telemetry/connection per remote relay",
        params={},
        returns="health summary",
    )
)

_register(
    OperationDef(
        name="capacity-snapshot",
        description="Current slot usage from recent execution events",
        params={},
        returns="capacity summary",
    )
)


def list_operations() -> list[dict[str, Any]]:
    """Return all operations as dicts for agent discovery."""
    return [
        {
            "name": op.name,
            "description": op.description,
            "params": op.params,
            "returns": op.returns,
        }
        for op in _OPERATIONS.values()
    ]


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
            "error": f"Unknown operation: {name}. Use 'operations' to list available."
        }

    try:
        return await _DISPATCH[name](params, store)
    except Exception as e:
        logger.exception("Operation %s failed", name) # Use logger.exception for traceback
        # Potentially return a more structured error, e.g., with an error_code
        return {"error": f"Operation failed: {e}", "error_type": e.__class__.__name__}


async def _recent_failures(params: dict[str, Any], store: Any) -> dict[str, Any]:
    limit = params.get("limit", 20)
    rows = await store.query(
        "SELECT * FROM events WHERE signal LIKE '%.failed' OR signal LIKE '%.error' "
        "ORDER BY seq DESC LIMIT ?",
        (limit,),
    )
    return {"rows": rows, "count": len(rows)}


async def _noise_profile(params: dict[str, Any], store: Any) -> dict[str, Any]:
    import time

    minutes = params.get("minutes", 5)
    cutoff = int(time.time() * 1000) - (minutes * 60 * 1000)
    rows = await store.query(
        "SELECT signal, COUNT(*) as count FROM events "
        "WHERE ts_unix_ms > ? GROUP BY signal ORDER BY count DESC",
        (cutoff,),
    )
    return {"signals": rows, "minutes": minutes}


async def _coordination_audit(params: dict[str, Any], store: Any) -> dict[str, Any]:
    limit = params.get("limit", 50)
    rows = await store.query(
        "SELECT * FROM events WHERE role = 'coordination' ORDER BY seq DESC LIMIT ?",
        (limit,),
    )
    return {"rows": rows, "count": len(rows)}


async def _model_timeline(params: dict[str, Any], store: Any) -> dict[str, Any]:
    model_id = params.get("model_id", "")
    if not model_id:
        return {"error": "model_id is required"}
    rows = await store.query(
        "SELECT * FROM events WHERE model_id = ? ORDER BY seq",
        (model_id,),
    )
    return {"rows": rows, "count": len(rows), "model_id": model_id}


async def _request_trace(params: dict[str, Any], store: Any) -> dict[str, Any]:
    request_id = params.get("request_id", "")
    if not request_id:
        return {"error": "request_id is required"}
    rows = await store.query(
        "SELECT * FROM events WHERE request_id = ? ORDER BY seq",
        (request_id,),
    )
    return {"rows": rows, "count": len(rows), "request_id": request_id}


async def _request_lifecycle(params: dict[str, Any], store: Any) -> dict[str, Any]:
    request_id = params.get("request_id", "")
    if not request_id:
        return {"error": "request_id is required"}
    rows = await store.query(
        "SELECT * FROM request_snapshots WHERE request_id = ? ORDER BY seq",
        (request_id,),
    )
    return {"rows": rows, "count": len(rows), "request_id": request_id}


async def _request_summary(params: dict[str, Any], store: Any) -> dict[str, Any]:
    import time

    minutes = params.get("minutes", 5)
    cutoff = int(time.time() * 1000) - (minutes * 60 * 1000)
    rows = await store.query(
        "SELECT phase, COUNT(*) as count FROM request_snapshots "
        "WHERE ts_unix_ms > ? GROUP BY phase",
        (cutoff,),
    )
    return {"phases": rows, "minutes": minutes}


async def _pipeline_trace(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    execution_id = params.get("execution_id", "")
    if not execution_id:
        return {"error": "execution_id is required"}
    rows = await store.query(
        "SELECT * FROM events WHERE execution_id = ? ORDER BY seq",
        (execution_id,),
    )
    steps: list[dict[str, Any]] = []
    total_tokens = 0
    for row in rows:
        payload = json.loads(row["payload"]) if row.get("payload") else {}
        step_info: dict[str, Any] = {
            "signal": row["signal"],
            "source": row["source"],
            "ts": row["timestamp"],
        }
        if "model_id" in payload:
            step_info["model"] = payload["model_id"]
        if "duration_ms" in payload:
            step_info["duration_ms"] = payload["duration_ms"]
        total_tokens_in = 0
        total_tokens_out = 0
        if "tokens_in" in payload:
            step_info["tokens_in"] = payload["tokens_in"]
            total_tokens_in += payload["tokens_in"]
        if "tokens_out" in payload:
            step_info["tokens_out"] = payload["tokens_out"]
            total_tokens_out += payload["tokens_out"]
    # ...
    return {
        "execution_id": execution_id,
        "steps": steps,
        "event_count": len(rows),
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out
    }
        if "status" in payload:
            step_info["status"] = payload["status"]
        steps.append(step_info)

    return {
        "execution_id": execution_id,
        "steps": steps,
        "event_count": len(rows),
        "total_tokens": total_tokens,
    }


async def _compare_runs(params: dict[str, Any], store: EventStore) -> dict[str, Any]:
    run_a = params.get("run_a", "")
    run_b = params.get("run_b", "")
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


async def _federation_health(params: dict[str, Any], store: Any) -> dict[str, Any]:
    rows = await store.query(
        "SELECT * FROM events WHERE signal LIKE 'federation.%' "
        "ORDER BY seq DESC LIMIT 50",
    )
    return {"rows": rows, "count": len(rows)}


async def _capacity_snapshot(params: dict[str, Any], store: Any) -> dict[str, Any]:
    rows = await store.query(
        "SELECT * FROM events "
        "WHERE signal IN ('model.execution.completed', 'model.execution.failed', "
        "'model.capacity.freed', 'model.loaded', 'model.unloaded') "
        "ORDER BY seq DESC LIMIT 50",
    )
    return {"rows": rows, "count": len(rows)}


from typing import Callable, Awaitable

OperationCallable = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]

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
}
