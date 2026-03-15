"""Event observability tools — macro-tool for querying the event service.

Connects to the event service via UDS (httpx with unix transport).
Single tool with operation enum minimizes context overhead for agents.
Follows the error envelope pattern from tools/rag.py.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_QUERY_SOCKET = os.environ.get(
    "EVENT_QUERY_SOCKET", "/tmp/universal-protocol/events-query.sock"
)
_QUERY_TIMEOUT = 10.0

_VALID_OPERATIONS = frozenset(
    {
        "recent-failures",
        "noise-profile",
        "coordination-audit",
        "model-timeline",
        "request-trace",
        "request-lifecycle",
        "request-summary",
        "pipeline-trace",
        "compare-runs",
        "federation-health",
        "capacity-snapshot",
        "signal-events",
        "stack-last-started",
        "operations",
        "raw_sql",
    }
)


def _query_event_service(body: dict[str, Any]) -> dict[str, Any]:
    """POST to event service query endpoint over UDS."""
    try:
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=_QUERY_SOCKET),
            timeout=_QUERY_TIMEOUT,
        ) as client:
            resp = client.post("http://localhost/v1/query", json=body)
            resp.raise_for_status()
            return resp.json()
    except FileNotFoundError as e:
        logger.error("Event service UDS socket not found: %s", e)
        return {
            "error": (
                "Event service socket not found. "
                "Start the event service via ./manage or docker compose."
            )
        }
    except httpx.ReadTimeout:
        return {
            "error": "Event query timed out. Try a narrower query or increase limits."
        }
    except httpx.ConnectError as e:
        logger.error("Event service not reachable: %s", e, exc_info=True)
        return {"error": f"Event service not reachable: {e}"}
    except httpx.HTTPStatusError as e:
        logger.error(
            "Event service returned error status: %s, response: %s",
            e.response.status_code,
            e.response.text,
            exc_info=True,
        )
        return {"error": f"Event service error: {e.response.status_code} - {e.response.text}"}
    except Exception as e:
        logger.error("Event query failed: %s", e, exc_info=True)
        return {"error": f"Event query failed: {e}"}


def register_event_tools(mcp: FastMCP) -> None:
    """Register event observability tools on the MCP server."""

    @mcp.tool()
    def query_observability(
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Query system telemetry, traces, and request snapshots.

        Single entry point for all event service operations. Use
        operation='operations' to discover available operations and
        their detailed parameter schemas.

        Default time window semantics are owned by Event Service operations.
        This tool forwards params through unchanged.

        Operations:
          recent-failures    — Failures/errors (window defaults in Event Service)
          noise-profile      — Signal frequency histogram (window defaults in Event Service)
          coordination-audit — Recent role=coordination events
          model-timeline     — Load/execute/unload for a model
          request-trace      — All events for a request_id
          request-lifecycle  — Snapshot phases for a request
          request-summary    — Aggregate request stats
          pipeline-trace     — Step-by-step execution trace
          compare-runs       — Side-by-side metrics for two runs
          federation-health  — Latest relay telemetry
          capacity-snapshot  — Current slot usage
          signal-events      — Recent events for a signal pattern, with payload
          stack-last-started — Per-service last startup timestamp + overall session start
          operations         — List all available operations
          raw_sql            — Raw SQL query (SELECT only, use "params" list for bindings)

        Authoritative operation set: _VALID_OPERATIONS in this module.

        Args:
            operation: Operation name from the list above.
            params: Operation-specific parameters (see 'operations' for schema).

        Returns:
            On success: operation-specific result dict
            On error: {"error": "<message>"}
        """
        if operation not in _VALID_OPERATIONS:
            return {
                "error": f"Unknown operation: {operation}. "
                f"Valid: {', '.join(sorted(_VALID_OPERATIONS))}"
            }

        t0 = monotonic_now()
        record("mcp.events.query.called", operation=operation)

        if operation == "operations":
            body: dict[str, Any] = {"type": "operations"}
            result = _query_event_service(body)
        elif operation == "raw_sql":
            params_dict = params or {}
            body = {
                "type": "sql",
                "sql": params_dict.get("sql", ""),
                "params": params_dict.get("params", []),
                "limit": params_dict.get("limit", 100),
            }
            result = _query_event_service(body)
        else:
            body = {"type": "operation", "name": operation, "params": params or {}}
            result = _query_event_service(body)

        duration = monotonic_now() - t0
        if "error" in result:
            record(
                "mcp.events.query.failed",
                operation=operation,
                error=result["error"],
                duration_s=round(duration, 3),
            )
        else:
            record(
                "mcp.events.query.completed",
                operation=operation,
                duration_s=round(duration, 3),
            )

        return result
