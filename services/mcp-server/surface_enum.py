"""Per-surface cortex op enum injection at tools/list time."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from _derive import derive_cortex_surface
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools.base import Tool

Surface = Literal["life", "code"]
_CORTEX_TOOL = "cortex"


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
    """Inject surface-scoped cortex op enum without mutating Tool.parameters."""

    def __init__(self, surface: Surface) -> None:
        self._surface = surface
        self._ops_enum = derive_cortex_surface(surface).ops_enum

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [
            _SurfaceEnumToolProxy(t, self._ops_enum)
            if t.name == _CORTEX_TOOL
            else t
            for t in tools
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        if tool and tool.name == _CORTEX_TOOL:
            return _SurfaceEnumToolProxy(tool, self._ops_enum)
        return tool


def register_surface_enum_transform(mcp: FastMCP, surface: Surface) -> None:
    """Register per-surface cortex op enum transform on a FastMCP instance."""
    mcp.add_transform(SurfaceEnumTransform(surface))
