"""Async tool executor for agent-seat tool loops.

Dispatches OpenAI-function-call ``tool_calls`` to local REST endpoints:
  - cortex:    POST /dispatch on cortex-api (unified op dispatch)
  - agent_bus: UDS REST on agent-bus
  - rag:       Stargate's rag-context pipeline via /v1/chat/completions

Returns a JSON string for each call (matches the tool-result payload shape
expected by OpenAI / Anthropic / xAI / Google adapters).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlencode

import httpx
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    make_async_client,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_RAG_TIMEOUT = 60.0
_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")


def _parse_dispatch_arguments(raw: Any) -> dict[str, Any] | None:
    """Parse dispatch-style arguments (JSON string or dict). None on failure."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


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
    except Exception:
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
    """Async agent-bus relay. Returns parsed JSON (list or dict)."""
    try:
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_DEFAULT_TIMEOUT
        ) as client:
            resp = await client.request(method.upper(), path, json=body)
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
    except Exception:
        return {"error": f"agent-bus returned invalid JSON: {resp.text[:200]}"}


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

    result = await _agent_bus_request(method, path, body)
    return json.dumps(result)


# ── RAG search via Stargate's rag-context pipeline ──────────────────────────


async def _execute_rag_search(args: dict[str, Any]) -> str:
    """Execute RAG search via Stargate's rag-context pipeline."""
    query = args.get("query", "")
    if not query:
        return json.dumps({"error": "rag_search requires 'query'"})

    body: dict[str, Any] = {
        "model": "rag-context",
        "messages": [{"role": "user", "content": query}],
    }
    scope = args.get("scope")
    if scope:
        body["pipeline_options"] = {"scope_override": scope}

    try:
        async with httpx.AsyncClient(timeout=_RAG_TIMEOUT) as client:
            resp = await client.post(f"{_STARGATE_URL}/v1/chat/completions", json=body)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        return json.dumps({"error": f"RAG search failed: {exc}"})

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        return json.dumps({"error": "RAG returned empty results"})
    return content


# ── Top-level dispatcher ────────────────────────────────────────────────────


async def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a single tool call to its REST backend. Returns JSON string.

    Supported tools (match libs/agent_seat/tools.TEAM_TOOL_DEFINITIONS):
      - ``cortex``     — unified op dispatch to cortex-api /dispatch
      - ``agent_bus``  — unified op dispatch to agent-bus
      - ``rag_search`` — RAG context retrieval via Stargate rag-context pipeline

    Unknown tool names return an error JSON string.
    """
    if name == "cortex":
        return await _execute_cortex(args)
    if name == "agent_bus":
        return await _execute_agent_bus(args)
    if name == "rag_search":
        return await _execute_rag_search(args)
    return json.dumps({"error": f"Unknown tool: {name}"})
