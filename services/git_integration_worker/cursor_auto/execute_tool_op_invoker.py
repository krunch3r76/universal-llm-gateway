"""Production tier-M tool-op invoker for cursor-auto ``execute`` jobs.

Wires the code-surface-reachable ratified read-only ops:
``observability.query``, ``cortex.search``, ``cortex.entity_get``.

``email.*`` allow rows bind at the life-MCP surface, not this worker's code
surface — those ops are intentionally unwired here; admission may pass but
execution refuses ``execute_invoker_unconfigured`` (spec §7 reachability caveat).

Registration happens once at worker lifespan start via :func:`register_production_invoker`.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.execute_runner import (
    INVOKER_UNCONFIGURED_REASON,
    InvokerUnconfiguredError,
    set_tool_op_invoker,
)

logger = get_logger(__name__)

_EVENTS_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_QUERY_TIMEOUT = 10.0
_CORTEX_TIMEOUT = 30.0

_WIRED_OPS: frozenset[tuple[str, str]] = frozenset(
    {
        ("observability", "query"),
        ("cortex", "search"),
        ("cortex", "entity_get"),
    }
)


def is_wired_tool_op(tool: str, op: str) -> bool:
    """Return whether this seat's production invoker fires *tool*.*op*."""
    return (tool, op) in _WIRED_OPS


def _relay_cortex_dispatch(op: str, arguments: dict[str, Any]) -> dict[str, Any]:
    body = {
        "tool": op,
        "arguments": json.dumps(arguments),
        "surface": "code",
        "via_adapter": True,
        "seat": "cursor-auto",
    }
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT) as client:
            response = client.post("/dispatch", json=body)
    except httpx.RequestError as exc:
        return {"error": f"cortex-api connection failed: {exc}", "status_code": None}
    if response.status_code >= 400:
        return {
            "error": f"cortex-api error: HTTP {response.status_code} — {response.text}",
            "status_code": response.status_code,
        }
    try:
        parsed = response.json()
    except ValueError:
        return {
            "error": f"cortex-api returned invalid JSON: {response.text[:200]}",
            "status_code": None,
        }
    if not isinstance(parsed, dict):
        return {"error": f"cortex-api returned {type(parsed).__name__}", "status_code": None}
    return parsed


def _relay_observability_query(arguments: dict[str, Any]) -> dict[str, Any]:
    operation = str(arguments.get("operation") or "").strip()
    if not operation:
        return {"error": "observability.query requires tool_args.operation"}
    params = arguments.get("params") or {}
    str(arguments.get("target") or "ulg")
    if operation == "operations":
        body: dict[str, Any] = {"type": "operations"}
    elif operation == "raw_sql":
        body = {
            "type": "sql",
            "sql": params.get("sql", ""),
            "params": params.get("params", []),
            "limit": params.get("limit", 100),
        }
    else:
        body = {"type": "operation", "name": operation, "params": params}
    url = f"unix://{_EVENTS_QUERY_SOCK}"
    try:
        with make_sync_client(url, timeout=_QUERY_TIMEOUT) as client:
            resp = client.post("/v1/query", json=body)
            resp.raise_for_status()
            payload = resp.json()
    except FileNotFoundError:
        return {"error": "Event service socket not found"}
    except httpx.HTTPError as exc:
        return {"error": f"Event service error: {exc}"}
    if not isinstance(payload, dict):
        return {"error": f"event service returned {type(payload).__name__}"}
    return payload


def _fire_tool_op_sync(*, tool: str, op: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not is_wired_tool_op(tool, op):
        raise InvokerUnconfiguredError(
            f"{tool}.{op} is allowlisted but not wired on this worker seat "
            f"({INVOKER_UNCONFIGURED_REASON})"
        )
    if tool == "cortex" and op in {"search", "entity_get"}:
        return _relay_cortex_dispatch(op, arguments)
    if tool == "observability" and op == "query":
        return _relay_observability_query(arguments)
    raise InvokerUnconfiguredError(f"no relay for {tool}.{op}")


async def production_tool_op_invoker(
    *,
    tool: str,
    op: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Async entry — relays through sync HTTP/UDS clients in a worker thread."""
    return await asyncio.to_thread(
        _fire_tool_op_sync,
        tool=tool,
        op=op,
        arguments=arguments,
    )


def register_production_invoker() -> None:
    """Install the worker's tier-M op invoker at process start."""
    set_tool_op_invoker(production_tool_op_invoker)
    logger.info(
        "cursor-auto tier-M invoker registered wired_ops=%s",
        sorted(".".join(pair) for pair in _WIRED_OPS),
    )


__all__ = [
    "is_wired_tool_op",
    "production_tool_op_invoker",
    "register_production_invoker",
]
