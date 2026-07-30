"""Production tier-M tool-op invoker for cursor-auto ``execute`` jobs.

Wires code-authority read-only ops (``observability.query``, ``cortex.search``,
``cortex.entity_get``) and, when :data:`EMAIL_BRIDGE_EXECUTE_RELAY_ENABLED` is
set, life-authority ``email.pull`` / ``email.search`` via
:mod:`email_bridge_relay` (email-bridge UDS — not life MCP).

Email relay registration defaults **off**; enabling requires operator DISPOSITION
(``cortex://notes/system/specs/life-code-execute-bridge.md``). ``email.send``,
``email.move``, and ``email.delete`` stay manifest-denied and unwired.

Registration happens once at worker lifespan start via :func:`register_production_invoker`.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.email_bridge_relay import (
    EMAIL_BRIDGE_EXECUTE_RELAY_FLAG,
    email_bridge_execute_relay_enabled,
    relay_email_pull,
    relay_email_search,
)
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

RelayFn = Callable[[dict[str, Any]], dict[str, Any]]


def _base_relay_registry() -> dict[tuple[str, str], RelayFn]:
    """Code-surface relays always registered; email entries added when flag on."""
    registry: dict[tuple[str, str], RelayFn] = {
        ("observability", "query"): _relay_observability_query,
        ("cortex", "search"): lambda args: _relay_cortex_dispatch("search", args),
        ("cortex", "entity_get"): lambda args: _relay_cortex_dispatch("entity_get", args),
    }
    if email_bridge_execute_relay_enabled():
        registry[("email", "pull")] = relay_email_pull
        registry[("email", "search")] = relay_email_search
    return registry


def relay_registry() -> dict[tuple[str, str], RelayFn]:
    """Return the active ``(tool, op) → relay_fn`` map for this process."""
    return _base_relay_registry()


def is_wired_tool_op(tool: str, op: str) -> bool:
    """Return whether this seat's production invoker fires *tool*.*op*."""
    return (tool, op) in relay_registry()


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
    registry = relay_registry()
    relay_fn = registry.get((tool, op))
    if relay_fn is None:
        raise InvokerUnconfiguredError(
            f"{tool}.{op} is allowlisted but not wired on this worker seat "
            f"({INVOKER_UNCONFIGURED_REASON})"
        )
    return relay_fn(arguments)


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
    wired = sorted(".".join(pair) for pair in relay_registry())
    logger.info(
        "cursor-auto tier-M invoker registered wired_ops=%s flag=%s=%s",
        wired,
        EMAIL_BRIDGE_EXECUTE_RELAY_FLAG,
        email_bridge_execute_relay_enabled(),
    )


__all__ = [
    "is_wired_tool_op",
    "production_tool_op_invoker",
    "register_production_invoker",
    "relay_registry",
]
