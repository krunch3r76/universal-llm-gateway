"""Per-surface dispatch-tool op enum injection at tools/list time."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from _derive import derive_cortex_surface
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.utilities.versions import VersionSpec
from tools.agent_bus import advertised_agent_bus_ops

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools.base import Tool

Surface = Literal["life", "code"]
_CORTEX_TOOL = "cortex"
_AGENT_BUS_TOOL = "agent_bus"


class _SurfaceEnumToolProxy:
    """Delegate to a Tool but inject per-surface op enum on the wire schema."""

    __slots__ = ("_tool", "_ops_enum")

    def __init__(self, tool: Tool, ops_enum: tuple[str, ...]) -> None:
        self._tool = tool
        self._ops_enum = ops_enum

    def to_mcp_tool(self, **overrides: Any) -> Any:
        mcp_tool = self._tool.to_mcp_tool(**overrides)
        schema = mcp_tool.inputSchema
        if isinstance(schema, dict):
            props = schema.get("properties")
            if isinstance(props, dict) and "tool" in props:
                tool_prop = dict(props["tool"]) if isinstance(props["tool"], dict) else {}
                tool_prop["enum"] = list(self._ops_enum)
                props = dict(props)
                props["tool"] = tool_prop
                schema = dict(schema)
                schema["properties"] = props
                mcp_tool.inputSchema = schema
        return mcp_tool

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool, name)


class SurfaceEnumTransform(Transform):
    """Inject surface-scoped dispatch op enums without mutating Tool.parameters."""

    def __init__(self, surface: Surface) -> None:
        self._surface = surface
        self._cortex_ops_enum = derive_cortex_surface(surface).ops_enum
        self._agent_bus_ops_enum = advertised_agent_bus_ops()

    def _ops_enum_for(self, tool_name: str) -> tuple[str, ...] | None:
        if tool_name == _CORTEX_TOOL:
            return self._cortex_ops_enum
        if tool_name == _AGENT_BUS_TOOL:
            return self._agent_bus_ops_enum
        return None

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        result: list[Tool] = []
        for t in tools:
            ops_enum = self._ops_enum_for(t.name)
            result.append(
                _SurfaceEnumToolProxy(t, ops_enum) if ops_enum is not None else t
            )
        return result

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        if not tool:
            return None
        ops_enum = self._ops_enum_for(tool.name)
        if ops_enum is not None:
            return _SurfaceEnumToolProxy(tool, ops_enum)
        return tool


def register_surface_enum_transform(mcp: FastMCP, surface: Surface) -> None:
    """Register per-surface dispatch op enum transforms on a FastMCP instance."""
    mcp.add_transform(SurfaceEnumTransform(surface))
