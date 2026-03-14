"""Event observability tools — macro-tool for querying the event service.

Connects to the event service via UDS (httpx with unix transport).
Single tool with operation enum minimizes context overhead for agents.
Follows the error envelope pattern from tools/rag.py.
"""

from __future__ import annotations

import logging
import os
import time
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

# All signals that indicate a service started — used by stack-last-started.
_STARTUP_SIGNALS: frozenset[str] = frozenset(
    {
        "system.started",  # Stargate (canonical session boundary)
        "cloud.proxy.started",  # Cloud Proxy
        "rag.started",  # RAG service
    }
)

# Canonical session boundary signal.
# ∀ per-service restarts (RAG, cloud proxy): NOT a session boundary.
_SESSION_BOUNDARY_SIGNAL = "system.started"

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


def _get_session_start_ts() -> int | None:
    """Return ts_unix_ms of the most recent Stargate startup, or None on failure.

    Uses system.started exclusively — the canonical session boundary signal.
    RAG/cloud-proxy independent restarts do NOT shift this value, ensuring
    the default observability window matches the full session, not just
    the last sub-service restart.

    Returns:
        int | None: Unix timestamp in milliseconds of the most recent
        Stargate startup, or None if not found or query failed.
    """
    result = _query_event_service(
        {
            "type": "sql",
            "sql": "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = ?",
            "params": [_SESSION_BOUNDARY_SIGNAL],
            "limit": 1,
        }
    )
    rows = result.get("rows", [])
    if rows and rows[0].get("ts") is not None:
        return int(rows[0]["ts"])
    return None


def _query_event_service(body: dict[str, Any]) -> dict[str, Any]:
    """POST to event service query endpoint over UDS."""
    transport = httpx.HTTPTransport(uds=_QUERY_SOCKET)
    try:
        with httpx.Client(transport=transport, timeout=_QUERY_TIMEOUT) as client:
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
            "Event service returned error status: %s", e.response.status_code, exc_info=True
        )
        return {"error": f"Event service error: {e.response.status_code}"}
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
        their parameters.

        Operations:
          recent-failures    — Failures/errors since last Stargate restart (override: since_ts param)
          noise-profile      — Signal frequency histogram since last restart (override: minutes param)
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
            p = params or {}
            body = {
                "type": "sql",
                "sql": p.get("sql", ""),
                "params": p.get("params", []),
                "limit": p.get("limit", 100),
            }
            result = _query_event_service(body)
        elif operation == "stack-last-started":
            # Use a JOIN to guarantee ts_unix_ms and timestamp come from the same row.
            signal_params = sorted(_STARTUP_SIGNALS)
            placeholders = ", ".join("?" for _ in signal_params)
            result = _query_event_service(
                {
                    "type": "sql",
                    "sql": (
                        "SELECT e.signal, e.ts_unix_ms, e.timestamp "
                        "FROM events e "
                        "JOIN ("
                        f"  SELECT signal, MAX(ts_unix_ms) AS max_ts FROM events "
                        f"  WHERE signal IN ({placeholders}) GROUP BY signal"
                        ") latest ON e.signal = latest.signal AND e.ts_unix_ms = latest.max_ts "
                        "ORDER BY e.ts_unix_ms DESC"
                    ),
                    "params": signal_params,
                    "limit": len(signal_params),
                }
            )
            rows = result.get("rows", [])
            session_start_ts = _get_session_start_ts()
            result["stack_start_ts_unix_ms"] = session_start_ts
            result["stack_start_timestamp"] = next(
                (
                    r.get("timestamp")
                    for r in rows
                    if r.get("signal") == _SESSION_BOUNDARY_SIGNAL
                ),
                None,
            )
        elif operation == "recent-failures":
            p = params or {}
            limit = int(p.get("limit", 20))
            since_ts_raw = p.get("since_ts")
            since_ts = int(since_ts_raw) if since_ts_raw is not None else None
            if since_ts is None:
                since_ts = _get_session_start_ts()
            if since_ts is not None:
                result = _query_event_service(
                    {
                        "type": "sql",
                        "sql": (
                            "SELECT * FROM events "
                            "WHERE (signal LIKE '%.failed' OR signal LIKE '%.error') "
                            "AND ts_unix_ms >= ? "
                            "ORDER BY ts_unix_ms DESC LIMIT ?"
                        ),
                        "params": [since_ts, limit],
                        "limit": limit,
                    }
                )
            else:
                result = _query_event_service(
                    {"type": "operation", "name": "recent-failures", "params": p}
                )
        elif operation == "noise-profile":
            p = params or {}
            minutes_raw = p.get("minutes")
            minutes = int(minutes_raw) if minutes_raw is not None else None
            if minutes is None:
                start_ts = _get_session_start_ts()
                if start_ts is not None:
                    elapsed_ms = int(time.time() * 1000) - start_ts
                    minutes = max(1, elapsed_ms // 60_000 + 1)
                else:
                    minutes = 5
            result = _query_event_service(
                {
                    "type": "operation",
                    "name": "noise-profile",
                    "params": {"minutes": minutes},
                }
            )
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
