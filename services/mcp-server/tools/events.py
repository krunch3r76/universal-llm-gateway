"""Event observability tools — macro-tool for querying the event service.

Connects to the event service via UDS (httpx with unix transport).
Single tool with operation enum minimizes context overhead for agents.
Follows the error envelope pattern from tools/rag.py.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record
from transport_utils import make_sync_client

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_QUERY_SOCKET = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_QUERY_TIMEOUT = 10.0
_CURSOR_PREVIEW_LIMIT = 50
_CURSOR_PREVIEW_BLOCKED = frozenset(
    {
        "raw_sql",
        "request-trace",
        "pipeline-trace",
        "request-lifecycle",
    }
)

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
        "realtime-snapshot",
        "operations",
        "raw_sql",
    }
)


@dataclass(frozen=True)
class _EventQueryTarget:
    name: str
    url: str


def _resolve_target(target: str) -> _EventQueryTarget | None:
    normalized = (target or "ulg").strip().lower()
    if normalized in {"", "default", "ulg"}:
        return _EventQueryTarget(
            name="ulg",
            url=f"unix://{_QUERY_SOCKET}",
        )
    return None


def _query_event_service(
    body: dict[str, Any], *, target: str = "ulg"
) -> dict[str, Any]:
    """POST a structured query payload to the selected event service instance.

    Returns the decoded response body. On transport/query failures, returns an
    error envelope with a human-readable `error` field for MCP callers.
    """
    resolved_target = _resolve_target(target)
    if resolved_target is None:
        return {
            "error": (
                f"Unknown observability target: {target}. "
                "Valid targets: ulg"
            )
        }
    try:
        client_ctx = make_sync_client(
            resolved_target.url,
            timeout=_QUERY_TIMEOUT,
        )
        with client_ctx as client:
            resp = client.post("/v1/query", json=body)
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
        return {
            "error": f"Event service error: {e.response.status_code} - {e.response.text}"
        }
    except Exception as e:
        logger.error("Event query failed: %s", e, exc_info=True)
        return {"error": f"Event query failed: {e}"}


def register_event_tools(mcp: FastMCP) -> None:
    """Register event observability tools on the MCP server."""

    @mcp.tool()
    def observability(
        operation: str,
        params: dict[str, Any] | None = None,
        target: str = "ulg",
    ) -> dict[str, Any]:
        """Query system telemetry, traces, and request snapshots from Event Service.

        Full docs: fs(op="md_read", sandbox="project", path="docs/tool-reference.md", section="observability")
        """
        if operation not in _VALID_OPERATIONS:
            return {
                "error": f"Unknown operation: {operation}. "
                f"Valid: {', '.join(sorted(_VALID_OPERATIONS))}"
            }

        t0 = monotonic_now()
        record("mcp.events.query.called", operation=operation, target=target)

        body: dict[str, Any]
        if operation == "operations":
            body = {"type": "operations"}
        elif operation == "raw_sql":
            params_dict = params or {}
            body = {
                "type": "sql",
                "sql": params_dict.get("sql", ""),
                "params": params_dict.get("params", []),
                "limit": params_dict.get("limit", 100),
            }
        else:
            body = {"type": "operation", "name": operation, "params": params or {}}
        result = _query_event_service(body, target=target)

        duration = monotonic_now() - t0
        if "error" in result:
            record(
                "mcp.events.query.failed",
                operation=operation,
                target=target,
                error=result["error"],
                duration_s=round(duration, 3),
            )
        else:
            record(
                "mcp.events.query.completed",
                operation=operation,
                target=target,
                duration_s=round(duration, 3),
            )

        return result

    @mcp.tool()
    def query_observability_preview(
        operation: str,
        params: dict[str, Any] | None = None,
        limit: int = 50,
        target: str = "ulg",
    ) -> dict[str, Any]:
        """Run bounded observability queries suitable for cursor_safe profile.

        Prefer this tool when the caller only needs a small recent slice of
        telemetry. Use `target="ulg"` for the default repo-wide instance.
        """
        if operation not in _VALID_OPERATIONS:
            return {
                "error": f"Unknown operation: {operation}. "
                f"Valid: {', '.join(sorted(_VALID_OPERATIONS))}"
            }
        if operation in _CURSOR_PREVIEW_BLOCKED:
            return {"error": f"Operation '{operation}' is not allowed in preview mode."}

        safe_limit = max(1, min(limit, _CURSOR_PREVIEW_LIMIT))
        params_dict = dict(params or {})
        try:
            requested_limit = int(params_dict.get("limit", safe_limit))
        except (TypeError, ValueError):
            requested_limit = safe_limit
        params_dict["limit"] = min(requested_limit, safe_limit)

        t0 = monotonic_now()
        record(
            "mcp.events.preview.called",
            operation=operation,
            target=target,
            limit=params_dict["limit"],
        )
        body = {"type": "operation", "name": operation, "params": params_dict}
        result = _query_event_service(body, target=target)
        duration = monotonic_now() - t0
        if "error" in result:
            record(
                "mcp.events.preview.failed",
                operation=operation,
                target=target,
                error=result["error"],
                duration_s=round(duration, 3),
            )
            return result

        record(
            "mcp.events.preview.completed",
            operation=operation,
            target=target,
            duration_s=round(duration, 3),
        )
        return result
