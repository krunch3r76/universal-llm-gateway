"""Execution dispatcher for event-store named operations.

The dispatcher binds catalog names to implementation callables and provides the
single error-handling boundary used by the query API. Catalog metadata remains
separate so discovery can be imported without loading every operation handler.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from .operation_catalog import get_operation
from .operation_types import OperationCallable
from .operations_delivery_audit import (
    _delivery_audit_artifacts,
    _delivery_audit_baseline_campaign,
    _delivery_audit_parent,
    _delivery_audit_selfassess,
    _delivery_audit_token_rollup,
)
from .operations_impl import (
    _coordination_audit,
    _model_timeline,
    _noise_profile,
    _recent_failures,
    _request_lifecycle,
    _request_summary,
    _request_trace,
    _signal_events,
)
from .operations_trace import (
    _capacity_snapshot,
    _compare_runs,
    _federation_health,
    _pipeline_trace,
    _provider_health,
    _realtime_snapshot,
    _stack_last_started,
    _verify_tool_execution,
)
from .store import EventStore

logger = get_logger(__name__)

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
    "provider-health": _provider_health,
    "capacity-snapshot": _capacity_snapshot,
    "signal-events": _signal_events,
    "stack-last-started": _stack_last_started,
    "realtime-snapshot": _realtime_snapshot,
    "verify-tool-execution": _verify_tool_execution,
    "delivery-audit-parent": _delivery_audit_parent,
    "delivery-audit-artifacts": _delivery_audit_artifacts,
    "delivery-audit-token-rollup": _delivery_audit_token_rollup,
    "delivery-audit-baseline-campaign": _delivery_audit_baseline_campaign,
    "delivery-audit-selfassess": _delivery_audit_selfassess,
}


async def execute_operation(
    name: str,
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    """Execute a registered named operation against ``store``.

    Unknown operation names produce the JSON-RPC-compatible ``-32601`` error
    payload expected by existing callers. Handler exceptions are logged and
    converted to a stable error envelope so the HTTP query layer stays thin.
    """
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
