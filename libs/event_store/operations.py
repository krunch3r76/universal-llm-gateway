"""Named operations catalog - discoverable pre-built queries."""

from __future__ import annotations

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

_register(
    OperationDef(
        name="verify-tool-execution",
        description="Most recent completed or failed event for a given MCP tool signal prefix",
        params={
            "signal_prefix": {"type": "string", "required": True},
            "since_ts": {"type": "int"},
        },
        returns="most recent outcome event (completed/failed) or empty",
    )
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


from .operations_impl import (  # noqa: E402
    _coordination_audit,
    _model_timeline,
    _noise_profile,
    _recent_failures,
    _request_lifecycle,
    _request_summary,
    _request_trace,
    _signal_events,
)
from .operations_trace import (  # noqa: E402
    _capacity_snapshot,
    _compare_runs,
    _federation_health,
    _pipeline_trace,
    _realtime_snapshot,
    _stack_last_started,
    _verify_tool_execution,
)

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
    "verify-tool-execution": _verify_tool_execution,
}


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
