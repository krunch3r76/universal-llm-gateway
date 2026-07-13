"""MCP delegate tool — thin relay to Stargate POST /api/v1/life/intent/*."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.utilities.versions import VersionSpec
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

from ._agent_tools import JsonArgStr, parse_dispatch_arguments

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastmcp import FastMCP
    from fastmcp.tools.base import Tool

_PROPOSE_OP = "propose"
_COMMIT_OP = "commit"
_OPS = frozenset({_PROPOSE_OP, _COMMIT_OP})
_TIMEOUT = 30.0
_DELEGATE_TOOL = "delegate"


def render_intent_input_schema() -> dict[str, Any]:
    """Intent object schema with verb enum from the life-intent registry."""
    from life_intent.registry import load_registry

    return load_registry().render_intent_input_schema()


def _relay_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    with make_sync_client(DEFAULT_STARGATE_URL, timeout=_TIMEOUT) as client:
        try:
            response = client.post(path, json=body)
        except httpx.RequestError as exc:
            return {"error": f"life-intent relay failed: {exc}", "status_code": None}
    try:
        return response.json()
    except ValueError:
        return {
            "error": f"invalid JSON from life-intent route: {response.text[:200]}",
            "status_code": response.status_code,
        }


class _DelegateSchemaProxy:
    """Inject registry-backed intent verb enum into delegate inputSchema."""

    __slots__ = ("_tool", "_intent_schema")

    def __init__(self, tool: Tool, intent_schema: dict[str, Any]) -> None:
        self._tool = tool
        self._intent_schema = intent_schema

    def to_mcp_tool(self, **overrides: Any) -> Any:
        mcp_tool = self._tool.to_mcp_tool(**overrides)
        schema = mcp_tool.inputSchema
        if isinstance(schema, dict):
            props = dict(schema.get("properties") or {})
            props["intent"] = {
                **self._intent_schema,
                "description": (
                    "Propose payload when op=propose. Pass the same object inside "
                    'arguments JSON as {"intent": {...}}.'
                ),
            }
            op_prop = dict(props.get("op") or {"type": "string"})
            op_prop["enum"] = sorted(_OPS)
            props["op"] = op_prop
            schema = dict(schema)
            schema["properties"] = props
            mcp_tool.inputSchema = schema
        return mcp_tool

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool, name)


class DelegateSchemaTransform(Transform):
    """Serve registry-backed verb enum on the delegate tool wire schema."""

    def __init__(self, intent_schema: dict[str, Any]) -> None:
        self._intent_schema = intent_schema

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [
            _DelegateSchemaProxy(tool, self._intent_schema)
            if tool.name == _DELEGATE_TOOL
            else tool
            for tool in tools
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        if tool and tool.name == _DELEGATE_TOOL:
            return _DelegateSchemaProxy(tool, self._intent_schema)
        return tool


def register_delegate_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Delegate")
    def delegate(op: str, arguments: JsonArgStr = "{}") -> Any:
        """Life intent — propose a work order or commit a frozen proposal.

        op=propose accepts cortex.life-intent/v1 intent fields and returns a
        work_order plus proposal_id without side effects. op=commit applies a
        prior proposal; when commit live-fire is gated off the route returns a
        typed reject unchanged. Replies arrive on this thread.
        """
        parsed = parse_dispatch_arguments(arguments)
        if parsed is None:
            return {"error": "arguments must be a JSON object", "status_code": 422}
        if op not in _OPS:
            return {
                "error": f"Unknown delegate op {op!r}. Available: {sorted(_OPS)}",
                "status_code": 422,
            }
        if op == _PROPOSE_OP:
            return _relay_post("/api/v1/life/intent/propose", parsed)
        return _relay_post("/api/v1/life/intent/commit", parsed)


def register_delegate_schema_transform(mcp: FastMCP) -> None:
    mcp.add_transform(DelegateSchemaTransform(render_intent_input_schema()))
