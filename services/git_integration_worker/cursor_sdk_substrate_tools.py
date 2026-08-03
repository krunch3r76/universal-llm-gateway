"""Host-side substrate custom tools for cursor-sdk dispatches (slice-1 read-only).

Catalog (cost-to-us ranked, architecture bind item 5):
  substrate_cortex_read → substrate_bus_tip → substrate_event_read
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Any

import httpx
from cursor_sdk.types import CustomTool
from transport_utils import DEFAULT_AGENT_BUS_URL, DEFAULT_CORTEX_URL, make_sync_client

_EVENTS_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_CORTEX_TIMEOUT = 30.0
_BUS_TIMEOUT = 15.0
_QUERY_TIMEOUT = 10.0

_CATALOG_ORDER = (
    "substrate_cortex_read",
    "substrate_bus_tip",
    "substrate_event_read",
)


@dataclass(frozen=True, slots=True)
class SubstrateDispatchContext:
    """Dispatch-scoped context for substrate op execute closures."""

    dispatch_id: str
    thread_id: str


def _relay_cortex_entity_get(arguments: dict[str, Any]) -> dict[str, Any]:
    body = {
        "tool": "entity_get",
        "arguments": json.dumps(arguments),
        "surface": "code",
        "via_adapter": True,
        "seat": "cursor-sdk",
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


def _relay_bus_tip(*, thread_id: str) -> dict[str, Any]:
    headers: dict[str, str] = {}
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=_BUS_TIMEOUT) as client:
            thread_resp = client.get(f"/threads/{thread_id}", headers=headers)
            turns_resp = client.get(
                "/turns",
                params={"thread": thread_id, "last": 1},
                headers=headers,
            )
    except httpx.RequestError as exc:
        return {"error": f"agent-bus connection failed: {exc}", "status_code": None}
    out: dict[str, Any] = {"thread_id": thread_id}
    if thread_resp.status_code < 400:
        try:
            out["thread"] = thread_resp.json()
        except ValueError:
            out["thread_raw"] = thread_resp.text[:500]
    else:
        out["thread_error"] = f"HTTP {thread_resp.status_code}"
    if turns_resp.status_code < 400:
        try:
            payload = turns_resp.json()
            turns = payload.get("turns") or []
            out["latest_turn"] = turns[-1] if turns else None
            out["turn_count_hint"] = len(turns)
        except ValueError:
            out["turns_raw"] = turns_resp.text[:500]
    else:
        out["turns_error"] = f"HTTP {turns_resp.status_code}"
    return out


def _relay_event_query(arguments: dict[str, Any]) -> dict[str, Any]:
    operation = str(arguments.get("operation") or "").strip()
    if not operation:
        return {"error": "substrate_event_read requires operation"}
    params = arguments.get("params") or {}
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


def build_substrate_custom_tools(
    ctx: SubstrateDispatchContext,
) -> dict[str, CustomTool]:
    """Return slice-1 read-only substrate catalog as host CustomTool entries."""

    def cortex_read(args: dict[str, Any], _tool_ctx: Any) -> str:
        entity_id = str(args.get("entity_id") or "").strip()
        if not entity_id:
            return json.dumps({"error": "entity_id is required"})
        intent = str(args.get("intent") or "card").strip()
        payload = _relay_cortex_entity_get({"entity_id": entity_id, "intent": intent})
        return json.dumps(payload, default=str)

    def bus_tip(args: dict[str, Any], _tool_ctx: Any) -> str:
        thread_id = str(args.get("thread_id") or ctx.thread_id).strip()
        if not thread_id:
            return json.dumps({"error": "thread_id is required"})
        return json.dumps(_relay_bus_tip(thread_id=thread_id), default=str)

    def event_read(args: dict[str, Any], _tool_ctx: Any) -> str:
        merged = dict(args)
        params = dict(merged.get("params") or {})
        if ctx.dispatch_id and "dispatch_id" not in params:
            params.setdefault("dispatch_id", ctx.dispatch_id)
        merged["params"] = params
        return json.dumps(_relay_event_query(merged), default=str)

    return {
        "substrate_cortex_read": CustomTool(
            execute=cortex_read,
            description=(
                "Host-resolved cortex entity read (entity_get-shaped). "
                "Use instead of vortex MCP cortex for read-only entity cards."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "intent": {
                        "type": "string",
                        "enum": ["card", "body", "full"],
                        "default": "card",
                    },
                },
                "required": ["entity_id"],
            },
        ),
        "substrate_bus_tip": CustomTool(
            execute=bus_tip,
            description=(
                "Read agent-bus thread tip (latest turn + thread status). "
                "Defaults thread_id to the dispatch thread."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                },
            },
        ),
        "substrate_event_read": CustomTool(
            execute=event_read,
            description=(
                "Host-resolved event/observability read for own-effect verification. "
                "Pass operation (+ optional params); dispatch_id injected when absent."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["operation"],
            },
        ),
    }


def merge_substrate_tools(
    local: Any,
    ctx: SubstrateDispatchContext | None,
) -> Any:
    """Attach substrate custom_tools to LocalAgentOptions when ctx is set."""
    if ctx is None:
        return local
    substrate = build_substrate_custom_tools(ctx)
    existing = dict(getattr(local, "custom_tools", None) or {})
    existing.update(substrate)
    return replace(local, custom_tools=existing)


__all__ = [
    "SubstrateDispatchContext",
    "build_substrate_custom_tools",
    "merge_substrate_tools",
    "_CATALOG_ORDER",
]
