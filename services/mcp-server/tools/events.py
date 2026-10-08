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

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_QUERY_SOCKET = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_CLAUDEBURST_QUERY_SOCKET = os.environ.get(
    "CLAUDEBURST_EVENTS_QUERY_SOCK",
    "/tmp/universal-protocol/claudeburst-events-query.sock",
)
_VALID_TARGETS = ("ulg", "claudeburst")
_QUERY_TIMEOUT = 10.0
_CURSOR_PREVIEW_LIMIT = 50
_CURSOR_PREVIEW_BLOCKED = frozenset(
    {
        "raw_sql",
        "sql",
        "request-trace",
        "pipeline-trace",
        "request-lifecycle",
    }
)

_ORIGIN_PREFIX = "/api/v1/observability"


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
    if normalized == "claudeburst":
        return _EventQueryTarget(
            name="claudeburst",
            url=f"unix://{_CLAUDEBURST_QUERY_SOCKET}",
        )
    return None


def _query_path(operation: str) -> str:
    if operation == "operations":
        return _ORIGIN_PREFIX
    return f"{_ORIGIN_PREFIX}/{operation}"


def _query_event_service(
    operation: str,
    params: dict[str, Any] | None = None,
    *,
    target: str = "ulg",
) -> dict[str, Any]:
    """Call the origin resource for one observability member."""
    from event_store.query_client import list_members, query_member, query_sql

    resolved_target = _resolve_target(target)
    if resolved_target is None:
        return {
            "error": (
                f"Unknown observability target: {target}. "
                f"Valid targets: {', '.join(_VALID_TARGETS)}"
            )
        }
    url = resolved_target.url
    try:
        if operation == "operations":
            return list_members(url=url, timeout=_QUERY_TIMEOUT)
        if operation in ("raw_sql", "sql"):
            params_dict = params or {}
            try:
                sql_limit = int(params_dict.get("limit", 100))
            except (TypeError, ValueError):
                return {"error": "limit must be an integer"}
            return query_sql(
                str(params_dict.get("sql") or ""),
                params=params_dict.get("params") or None,
                limit=sql_limit,
                url=url,
                timeout=_QUERY_TIMEOUT,
            )
        return query_member(
            operation, params or {}, url=url, timeout=_QUERY_TIMEOUT
        )
    except FileNotFoundError as e:
        logger.error("Event service UDS socket not found: %s", e)
        return {
            "error": (
                "Event service socket not found. "
                "Start the event service via ./manage or docker compose."
            )
        }


def register_event_tools(mcp: FastMCP) -> None:
    """Register event observability tools on the MCP server."""

    @mcp.tool(title="Observability")
    def observability(
        operation: str,
        params: dict[str, Any] | None = None,
        target: str = "ulg",
    ) -> dict[str, Any]:
        """Query system telemetry, traces, and request snapshots from Event Service.

        operation: named operation (see table below)
        params: dict with operation-specific parameters (optional)
        target: event service target — "ulg" (default fleet) or "claudeburst"
                 (perps embedded event store on claudeburst-events-query.sock)

        operation is any member name. operation="operations" returns every
        member with its declared params. operation="sql" or "raw_sql" POSTs the sql member.

        Example:
          observability(operation="recent-failures", params={"limit": 20})
          observability(operation="pipeline-trace", params={"execution_id": "abc123"})
        «verb-orientation:observability»
        Depth: `agent_skill:event-instrumentation-discipline` · `agent_skill:completion-provenance-discipline`.
        cursor_only (fs, not on the Customize loader):
        - `debug-with-events` — fs(sandbox="workspaces", op="read", path="universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/debug-with-events/SKILL.md")
        - `ulg-architecture` — fs(sandbox="workspaces", op="read", path="universal-llm-gateway/.cursor/skills/ulg-architecture/SKILL.md")
        - `mcp-tool-loop-trace-matrix` — fs(sandbox="workspaces", op="read", path="universal-llm-gateway/.cursor/skills/mcp-tool-loop-trace-matrix/SKILL.md")
        «/verb-orientation:observability»
        """
        path = _query_path(operation)
        t0 = monotonic_now()
        record(
            "mcp.events.query.called",
            operation=operation,
            target=target,
            path=path,
        )
        result = _query_event_service(operation, params, target=target)

        duration = monotonic_now() - t0
        if "error" in result:
            record(
                "mcp.events.query.failed",
                operation=operation,
                target=target,
                path=path,
                error=result["error"],
                duration_s=round(duration, 3),
            )
        else:
            record(
                "mcp.events.query.completed",
                operation=operation,
                target=target,
                path=path,
                duration_s=round(duration, 3),
            )
            result["_next"] = (
                "If this investigation surfaced a root cause, discovery, or "
                "non-obvious insight, record it via "
                "cortex observe on the relevant service: entity"
            )

        return result

    @mcp.tool(title="Observability Preview")
    def query_observability_preview(
        operation: str,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
        target: str = "ulg",
    ) -> dict[str, Any]:
        """Run bounded observability queries suitable for cursor_safe profile.

        Prefer this tool when the caller only needs a small recent slice of
        telemetry. Use `target="ulg"` for fleet events or `target="claudeburst"`
        for claudeburst.perps.* signals on the embedded perps event store.
        """
        if operation in _CURSOR_PREVIEW_BLOCKED:
            return {"error": f"Operation '{operation}' is not allowed in preview mode."}

        params_dict = dict(params or {})
        if limit is not None or "limit" in params_dict:
            raw_limit = params_dict.get("limit", limit)
            try:
                requested_limit = int(raw_limit)
            except (TypeError, ValueError):
                requested_limit = _CURSOR_PREVIEW_LIMIT
            params_dict["limit"] = max(1, min(requested_limit, _CURSOR_PREVIEW_LIMIT))

        t0 = monotonic_now()
        record(
            "mcp.events.preview.called",
            operation=operation,
            target=target,
            limit=params_dict["limit"],
        )
        result = _query_event_service(operation, params_dict, target=target)
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
