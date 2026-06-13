"""Discovery catalog for event-store named operations.

This module owns the operation registry exposed to agents through the query API.
It contains metadata only; executable handlers live in implementation modules
and are bound by ``operation_dispatch``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .operation_types import OperationDef

_OPERATIONS: dict[str, OperationDef] = {}


def _register(op: OperationDef) -> OperationDef:
    """Add one operation definition to the in-memory discovery registry."""
    _OPERATIONS[op.name] = op
    return op


_register(
    OperationDef(
        name="recent-failures",
        description="Last N failure/error/timeout events (default window: since last Stargate restart)",
        params={"limit": {"type": "int", "default": 20}, "since_ts": {"type": "int"}},
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
        params={"limit": {"type": "int", "default": 50}, "since_ts": {"type": "int"}},
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
        description=(
            "Step-by-step trace for a pipeline execution; returns "
            "pipeline_trace_aged_out when only the dispatch journal remains"
        ),
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
        params={"limit": {"type": "int", "default": 50}, "since_ts": {"type": "int"}},
        returns="health summary",
    )
)

_register(
    OperationDef(
        name="capacity-snapshot",
        description="Current slot usage from recent execution events (default window: since last Stargate restart)",
        params={"limit": {"type": "int", "default": 50}, "since_ts": {"type": "int"}},
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
        name="provider-health",
        description=(
            "Aggregate frontier generate health per provider from "
            "mcp.frontier.* and pipeline.frontier.dispatch.output.short "
            "signals (default window: since last Stargate restart). Returns "
            "called/completed/error counts, error-reason histogram, "
            "output-short fires (union of MCP + pipeline surfaces), "
            "mcp.frontier.tool.executed count, avg output_tokens, and "
            "avg duration_s."
        ),
        params={"minutes": {"type": "int"}, "provider": {"type": "string"}},
        returns="per-provider health summary",
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

_register(
    OperationDef(
        name="delivery-audit-parent",
        description=(
            "Fetch one B3 delivery-audit parent row from the sibling "
            "delivery-audit.db registry"
        ),
        params={
            "audit_id": {"type": "string"},
            "execution_id": {"type": "string"},
            "request_id": {"type": "string"},
            "dispatch_id": {"type": "string"},
        },
        returns="parent row or null plus lookup key used",
    )
)

_register(
    OperationDef(
        name="delivery-audit-artifacts",
        description=(
            "List delivered artifact rows for a B3 audit parent ordered by "
            "artifact_sequence"
        ),
        params={"audit_id": {"type": "string", "required": True}},
        returns="artifact rows plus count",
    )
)

_register(
    OperationDef(
        name="delivery-audit-token-rollup",
        description=(
            "Per-execution/session token-locality rollup derived from B3 "
            "delivered-artifact rows via derive_token_rollups"
        ),
        params={"audit_id": {"type": "string", "required": True}},
        returns="token rollup object plus count",
    )
)

_register(
    OperationDef(
        name="delivery-audit-baseline-campaign",
        description=(
            "Campaign p50/p95 token-locality report by workflow class and "
            "seat substrate from guidance workflow-summary rows"
        ),
        params={
            "campaign_id": {"type": "string", "required": True},
            "phase": {"type": "string", "default": "baseline"},
            "seat_substrate": {"type": "string"},
            "workflow_class": {"type": "string"},
        },
        returns="workflow-class percentile summaries plus N<50 p95 caveats",
    )
)

_register(
    OperationDef(
        name="delivery-audit-selfassess",
        description=(
            "Cross-seat §6 rubric self-assessment by workflow class and seat "
            "substrate from guidance workflow-summary rows"
        ),
        params={
            "campaign_id": {"type": "string", "required": True},
            "phase": {"type": "string", "default": "baseline"},
            "seat_substrate": {"type": "string"},
            "workflow_class": {"type": "string"},
        },
        returns=(
            "per-group rubric dimension verdicts plus summed token-expense vector"
        ),
    )
)

_register(
    OperationDef(
        name="frontier.densify.review.admitted",
        description=(
            "Densify review admitted events with opt-out / blank-hold tripwire "
            "readings over a time window"
        ),
        params={"minutes": {"type": "int"}, "limit": {"type": "int", "default": 200}},
        returns="admitted rows + opt_out_rate tripwire",
    )
)

_register(
    OperationDef(
        name="frontier.densify.review.outcome",
        description=(
            "Densify review outcome events with finding-delta / rubber-stamp "
            "tripwire readings over a time window"
        ),
        params={"minutes": {"type": "int"}, "limit": {"type": "int", "default": 200}},
        returns="outcome rows + finding_delta tripwire",
    )
)


def list_operations() -> list[dict[str, Any]]:
    """Return all registered operation definitions as serializable dicts."""
    return [asdict(op) for op in _OPERATIONS.values()]


def get_operation(name: str) -> OperationDef | None:
    """Return the operation definition for ``name`` if it is registered."""
    return _OPERATIONS.get(name)
