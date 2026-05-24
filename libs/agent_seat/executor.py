"""Async tool executor for agent-seat tool loops.

Dispatches OpenAI-function-call ``tool_calls`` to local REST endpoints:
  - cortex:    POST /dispatch on cortex-api (unified op dispatch)
  - agent_bus: UDS REST on agent-bus
  - rag:       live MCP ``rag`` tool
  - any other MCP tool: proxied through the live MCP server over JSON-RPC
    using the shared ``McpToolExecutor`` client already used by the cloud proxy

Returns a JSON string for each call (matches the tool-result payload shape
expected by OpenAI / Anthropic / xAI / Google adapters).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

if TYPE_CHECKING:
    from services.universal_cloud_proxy.mcp_executor import McpToolExecutor

from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    make_async_client,
)

from agent_seat.context import get_active_persona

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_MCP_EXECUTOR_LOCK = asyncio.Lock()
_MCP_EXECUTOR: McpToolExecutor | None = None
_MCP_EXECUTOR_INITIALIZED = False


def _parse_dispatch_arguments(raw: Any) -> dict[str, Any] | None:
    """Parse dispatch-style arguments (JSON string or dict). None on failure."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError as exc:
            logger.warning("dispatch arguments JSON invalid: %s", exc)
            return None
    return None


async def _get_mcp_executor() -> McpToolExecutor | None:
    """Return a started MCP JSON-RPC client, or ``None`` if unavailable.

    The frontier path runs inside Stargate rather than the MCP container, so we
    use the same public MCP endpoint configuration as the existing remote-MCP
    helpers: ``MCP_PUBLIC_URL`` + ``MCP_AUTH_TOKEN``. The executor discovers the
    live tool catalog once and reuses it for subsequent requests.
    """

    global _MCP_EXECUTOR, _MCP_EXECUTOR_INITIALIZED
    if _MCP_EXECUTOR_INITIALIZED:
        return _MCP_EXECUTOR

    async with _MCP_EXECUTOR_LOCK:
        if _MCP_EXECUTOR_INITIALIZED:
            return _MCP_EXECUTOR

        url = os.environ.get("MCP_PUBLIC_URL", "").strip()
        token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if not url or not token:
            logger.error(
                "agent_seat live MCP disabled — falling back to static tools only: "
                "MCP_PUBLIC_URL/MCP_AUTH_TOKEN not configured"
            )
            return None

        try:
            from services.universal_cloud_proxy.mcp_executor import McpToolExecutor
        except ImportError as exc:
            logger.error(
                "agent_seat live MCP disabled — falling back to static tools only: "
                "McpToolExecutor unavailable: %s",
                exc,
            )
            _MCP_EXECUTOR_INITIALIZED = True
            return None

        executor = McpToolExecutor(mcp_url=url, auth_token=token)
        await executor.startup()
        if executor.available:
            _MCP_EXECUTOR = executor
        else:
            logger.warning("agent_seat MCP executor discovered no tools from %s", url)
            return None
        _MCP_EXECUTOR_INITIALIZED = True
        return _MCP_EXECUTOR


async def get_mcp_tool_definitions() -> list[dict[str, Any]]:
    """Return live MCP tool defs converted to OpenAI function format."""

    executor = await _get_mcp_executor()
    if executor is None:
        return []
    return executor.get_openai_tool_defs()


async def resolve_tool_definitions(names: list[str]) -> list[dict[str, Any]]:
    """Resolve tool names against the static registry plus the live MCP catalog."""

    from agent_seat.tools import TOOL_REGISTRY

    definitions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for name in names:
        entry = TOOL_REGISTRY.get(name)
        if entry is not None:
            definitions.append(entry["definition"])
        else:
            unresolved.append(name)

    if not unresolved:
        return definitions

    live_defs = {
        d.get("function", {}).get("name", ""): d
        for d in await get_mcp_tool_definitions()
    }
    still_unknown: list[str] = []
    for name in unresolved:
        defn = live_defs.get(name)
        if defn is None:
            still_unknown.append(name)
        else:
            definitions.append(defn)

    if still_unknown:
        available = sorted(set(TOOL_REGISTRY) | {k for k in live_defs if k})
        raise ValueError(
            f"unknown tool {sorted(set(still_unknown))!r}; available: {available}"
        )
    return definitions


async def _cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Relay a single cortex op to cortex-api POST /dispatch."""
    try:
        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_DEFAULT_TIMEOUT
        ) as client:
            resp = await client.post(
                "/dispatch", json={"tool": tool, "arguments": arguments}
            )
    except Exception as exc:
        logger.error("cortex dispatch relay failed: %s %s — %s", tool, arguments, exc)
        return {"error": f"cortex-api connection failed: {exc}"}

    if resp.status_code >= 400:
        return {
            "error": f"cortex-api error: HTTP {resp.status_code}",
            "detail": resp.text[:500],
        }
    try:
        return resp.json()
    except Exception as exc:
        logger.warning("cortex-api returned invalid JSON: %s", exc)
        return {"error": f"cortex-api returned invalid JSON: {resp.text[:200]}"}


async def _execute_cortex(args: dict[str, Any]) -> str:
    """Execute a unified cortex(tool=..., arguments=...) call."""
    tool = args.get("tool", "")
    if not tool:
        return json.dumps({"error": "cortex: 'tool' is required"})
    parsed = _parse_dispatch_arguments(args.get("arguments", {}))
    if parsed is None:
        return json.dumps({"error": f"Invalid arguments JSON for cortex {tool!r}"})
    result = await _cortex_dispatch(tool, parsed)
    return json.dumps(result)


# ── Agent-bus operation table (unchanged — separate service) ────────────────


def _qs(params: dict[str, Any]) -> str:
    filtered = {k: v for k, v in params.items() if v is not None}
    return urlencode(filtered) if filtered else ""


def _bus_threads_path(args: dict[str, Any]) -> str:
    qs = _qs({"status": args.get("status", "active"), "tags": args.get("tags")})
    return f"/threads?{qs}" if qs else "/threads"


def _bus_fetch_path(args: dict[str, Any]) -> str:
    params: dict[str, Any] = {
        "thread": args.get("thread"),
        "to": args.get("to"),
        "last": args.get("last", 10),
        "compact": args.get("compact"),
    }
    if args.get("unread"):
        params["unread"] = "true"
    if args.get("mark_read"):
        params["mark_read"] = "true"
    qs = _qs(params)
    return f"/turns?{qs}"


def _bus_get_path(args: dict[str, Any]) -> str:
    thread = args.get("thread", "")
    turn_number = args.get("turn_number", 0)
    qs = _qs({"thread": thread, "turn_number": turn_number})
    return f"/turns/one?{qs}"


_BUS_OPS: dict[str, tuple[str, Any, str]] = {
    "threads": ("GET", _bus_threads_path, "none"),
    "fetch": ("GET", _bus_fetch_path, "none"),
    "get": ("GET", _bus_get_path, "none"),
    "post": ("POST", "/threads/with-turn", "args_as_is"),
    "reply": ("POST", "/turns", "args_as_is"),
}


async def _agent_bus_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """Async agent-bus relay. Returns parsed JSON (list or dict).

    Injects the ``AGENT_BUS_TOKEN`` bearer (Stargate env, same token used by
    Stargate's async-dispatch ``result_delivery`` path and the MCP-server's
    ``_local_relay``). Without this header, agent-bus's ``require_token``
    returns 401, breaking persona-side ``agent_bus`` calls from
    ``team_dispatch`` tool loops (e.g. Orion posting completion briefs).
    """
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if not token:
        logger.error(
            "agent_seat agent_bus call missing AGENT_BUS_TOKEN — agent-bus "
            "will reject with 401. Set AGENT_BUS_TOKEN in the Stargate "
            "container env."
        )
        return {
            "error": (
                "agent-bus auth not configured: AGENT_BUS_TOKEN missing from "
                "Stargate environment"
            )
        }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_DEFAULT_TIMEOUT
        ) as client:
            resp = await client.request(
                method.upper(), path, json=body, headers=headers
            )
    except Exception as exc:
        logger.error("agent-bus relay failed: %s %s — %s", method, path, exc)
        return {"error": f"agent-bus connection failed: {exc}"}

    if resp.status_code >= 400:
        return {
            "error": f"agent-bus error: HTTP {resp.status_code}",
            "detail": resp.text[:500],
        }
    try:
        return resp.json()
    except Exception as exc:
        logger.warning("agent-bus returned invalid JSON: %s", exc)
        return {"error": f"agent-bus returned invalid JSON: {resp.text[:200]}"}


def _inject_bus_from_agent(body: dict[str, Any], *, op: str) -> bool:
    """Default ``from`` on post/reply when the dispatch loop bound active_persona."""
    if body.get("from") or body.get("from_agent"):
        return False
    persona = get_active_persona()
    if not persona:
        return False
    body["from"] = persona
    logger.debug(
        "agent_seat agent_bus %s: injected from=%r from active_persona context",
        op,
        persona,
    )
    return True


async def _execute_agent_bus(args: dict[str, Any]) -> str:
    """Execute a unified agent_bus(tool=..., arguments=...) call."""
    tool = args.get("tool", "")
    spec = _BUS_OPS.get(tool)
    if spec is None:
        return json.dumps(
            {"error": f"Unknown agent_bus tool {tool!r}. Available: {sorted(_BUS_OPS)}"}
        )

    parsed = _parse_dispatch_arguments(args.get("arguments", "{}"))
    if parsed is None:
        return json.dumps({"error": f"Invalid arguments JSON for agent_bus {tool!r}"})

    method, path_spec, body_mode = spec
    path = path_spec(parsed) if callable(path_spec) else path_spec
    body: dict[str, Any] | None
    if body_mode == "none":
        body = None
    else:
        body = dict(parsed)
        if tool in ("post", "reply") and body is not None:
            _inject_bus_from_agent(body, op=tool)

    result = await _agent_bus_request(method, path, body)
    return json.dumps(result)


# ── Top-level dispatcher ────────────────────────────────────────────────────


async def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a single tool call to its REST backend. Returns JSON string.

    Supported tools (match libs/agent_seat/tools.TEAM_TOOL_DEFINITIONS):
      - ``cortex``       — unified op dispatch to cortex-api /dispatch
      - ``agent_bus``    — unified op dispatch to agent-bus
      - ``brave_search`` — Brave Search API via MCP (safe alias; MCP name: web_search)

    Unknown names fall back to the live MCP server catalog when available.
    """
    if name == "cortex":
        return await _execute_cortex(args)
    if name == "agent_bus":
        return await _execute_agent_bus(args)
    # brave_search is the safe alias for the MCP-side "web_search" tool.
    # ¬call "web_search" directly — name collides with native model capability.
    mcp_name = "web_search" if name == "brave_search" else name
    executor = await _get_mcp_executor()
    if executor is not None:
        return await executor.execute_tool(mcp_name, args)
    return json.dumps({"error": f"Unknown tool: {name}"})
