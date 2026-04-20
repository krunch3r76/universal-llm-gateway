"""Async tool executor for agent-seat tool loops.

Dispatches OpenAI-function-call ``tool_calls`` to local REST endpoints
(Cortex UDS, agent-bus UDS, RAG via Stargate's ``rag-context`` pipeline).
Independent of MCP server's sync executor — uses ``transport_utils``'s
async client factories and calls the same REST endpoints directly.

Returns a JSON string for each call (matches the tool-result payload shape
expected by OpenAI / Anthropic / xAI / Google adapters).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import quote, urlencode

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


async def _cortex_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Async Cortex relay. Mirrors MCP ``_cx`` error-normalization shape."""
    try:
        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_DEFAULT_TIMEOUT
        ) as client:
            resp = await client.request(method.upper(), path, json=body)
    except Exception as exc:
        logger.error("cortex relay failed: %s %s — %s", method, path, exc)
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


# ── Cortex operation table ──────────────────────────────────────────────────
# Each entry: op_name → (method, path_builder, body_builder)
# path_builder and body_builder take the parsed args dict and return the
# URL path / request body. None for body_builder = GET-style (no body).

CortexOp = tuple[str, Any, Any]


def _qs(params: dict[str, Any]) -> str:
    filtered = {k: v for k, v in params.items() if v is not None}
    return urlencode(filtered) if filtered else ""


def _cortex_entities_path(args: dict[str, Any]) -> str:
    qs = _qs({"type": args.get("type"), "limit": args.get("limit", 30)})
    return f"/entities?{qs}" if qs else "/entities"


def _cortex_entity_get_path(args: dict[str, Any]) -> str:
    entity_id = args.get("entity_id", "")
    return f"/entities/{quote(entity_id, safe=':')}"


def _cortex_assertions_path(args: dict[str, Any]) -> str:
    qs = _qs(
        {
            "entity_id": args.get("entity_id"),
            "confidence": args.get("confidence"),
            "limit": args.get("limit", 30),
        }
    )
    return f"/assertions?{qs}" if qs else "/assertions"


def _cortex_search_path(args: dict[str, Any]) -> str:
    qs = _qs({"q": args.get("query", ""), "limit": args.get("limit", 20)})
    return f"/search?{qs}" if qs else "/search"


def _cortex_edges_path(args: dict[str, Any]) -> str:
    qs = _qs(
        {
            "from_node": args.get("from_node"),
            "to_node": args.get("to_node"),
            "edge_type": args.get("edge_type"),
            "limit": args.get("limit", 30),
        }
    )
    return f"/edges?{qs}" if qs else "/edges"


def _cortex_edge_traverse_path(args: dict[str, Any]) -> str:
    qs = _qs(
        {
            "node": args.get("node", ""),
            "edge_type": args.get("edge_type"),
            "hops": args.get("hops", 3),
        }
    )
    return f"/edges/traverse?{qs}"


def _cortex_journal_read_path(args: dict[str, Any]) -> str:
    qs = _qs({"limit": args.get("limit", 10)})
    return f"/session-journals?{qs}" if qs else "/session-journals"


def _cortex_review_queue_path(args: dict[str, Any]) -> str:
    qs = _qs({"status": args.get("status", "pending"), "limit": args.get("limit", 20)})
    return f"/staging?{qs}"


_CORTEX_OPS: dict[str, tuple[str, Any, str]] = {
    # op -> (method, path_fn-or-str, body_mode)
    # body_mode: "none" (skip body), "args" (pass all args as body),
    #            "args_as_is" (pass args verbatim as body)
    "entities": ("GET", _cortex_entities_path, "none"),
    "entity_get": ("GET", _cortex_entity_get_path, "none"),
    "entity_create": ("POST", "/entities", "args_as_is"),
    "entity_update": (
        "PATCH",
        lambda a: f"/entities/{quote(a.get('entity_id', ''), safe=':')}",
        "args_as_is",
    ),
    "assertions": ("GET", _cortex_assertions_path, "none"),
    "assert": ("POST", "/assertions", "args_as_is"),
    "observe": ("POST", "/observations", "args_as_is"),
    "supersede": ("POST", "/assertions/supersede", "args_as_is"),
    "search": ("GET", _cortex_search_path, "none"),
    "deadlines": ("GET", "/deadlines", "none"),
    "journal_read": ("GET", _cortex_journal_read_path, "none"),
    "journal_write": ("POST", "/session-journals", "args_as_is"),
    "edge_create": ("POST", "/edges", "args_as_is"),
    "edges": ("GET", _cortex_edges_path, "none"),
    "edge_traverse": ("GET", _cortex_edge_traverse_path, "none"),
    "review_queue": ("GET", _cortex_review_queue_path, "none"),
}


async def _execute_cortex(args: dict[str, Any]) -> str:
    """Execute a unified cortex(tool=..., arguments=...) call."""
    tool = args.get("tool", "")
    spec = _CORTEX_OPS.get(tool)
    if spec is None:
        return json.dumps(
            {"error": f"Unknown cortex tool {tool!r}. Available: {sorted(_CORTEX_OPS)}"}
        )

    parsed = _parse_dispatch_arguments(args.get("arguments", "{}"))
    if parsed is None:
        return json.dumps({"error": f"Invalid arguments JSON for cortex {tool!r}"})

    method, path_spec, body_mode = spec
    path = path_spec(parsed) if callable(path_spec) else path_spec
    body: dict[str, Any] | None
    if body_mode == "none":
        body = None
    else:
        body = dict(parsed)

    result = await _cortex_request(method, path, body)
    return json.dumps(result)


# ── Agent-bus operation table ───────────────────────────────────────────────


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
    """Execute RAG search via Stargate's rag-context pipeline.

    Matches the MCP path's use of ``rag-context`` (query rewrite + parallel
    retrieval + RRF merge) so dispatched agents get the same quality RAG
    response shape as interactive MCP callers.
    """
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


# ── Read-only individual tool executors (match TOOL_DEFINITIONS names) ──────


async def _execute_cortex_entity_get(args: dict[str, Any]) -> str:
    entity_id = args.get("entity_id", "")
    return json.dumps(
        await _cortex_request("GET", f"/entities/{quote(entity_id, safe=':')}")
    )


async def _execute_cortex_search_entities(args: dict[str, Any]) -> str:
    return json.dumps(await _cortex_request("GET", _cortex_entities_path(args)))


async def _execute_cortex_assertions(args: dict[str, Any]) -> str:
    return json.dumps(await _cortex_request("GET", _cortex_assertions_path(args)))


async def _execute_cortex_deadlines(args: dict[str, Any]) -> str:
    return json.dumps(await _cortex_request("GET", "/deadlines"))


# ── Top-level dispatcher ────────────────────────────────────────────────────


async def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a single tool call to its REST backend. Returns JSON string.

    Supports both read-tier (individual ``cortex_*`` tools + ``rag_search``)
    and team-tier (unified ``cortex`` and ``agent_bus`` dispatch tools).
    Unknown tool names return an error JSON string.
    """
    if name == "cortex_entity_get":
        return await _execute_cortex_entity_get(args)
    if name == "cortex_search_entities":
        return await _execute_cortex_search_entities(args)
    if name == "cortex_assertions":
        return await _execute_cortex_assertions(args)
    if name == "cortex_deadlines":
        return await _execute_cortex_deadlines(args)
    if name == "rag_search":
        return await _execute_rag_search(args)
    if name == "cortex":
        return await _execute_cortex(args)
    if name == "agent_bus":
        return await _execute_agent_bus(args)
    return json.dumps({"error": f"Unknown tool: {name}"})
