"""Wire enum injection for agent_bus — deprecated post/reply omitted."""

from __future__ import annotations

from tools.agent_bus import (
    AGENT_BUS_DEPRECATED_OPS,
    AGENT_BUS_OPS,
    advertised_agent_bus_ops,
)


def test_advertised_agent_bus_ops_excludes_deprecated() -> None:
    advertised = frozenset(advertised_agent_bus_ops())
    assert AGENT_BUS_DEPRECATED_OPS.isdisjoint(advertised)
    assert "send" in advertised
    assert "request" in advertised
    assert "hop" in advertised
    assert "substrate_graph_write" in advertised
    assert "substrate_friction_file" in advertised
    assert "fetch" in advertised
    assert advertised | AGENT_BUS_DEPRECATED_OPS == frozenset(AGENT_BUS_OPS)


def test_dispatch_ops_match_openapi_mcp_denominator() -> None:
    from agent_bus_store.openapi_mcp._ops import AGENT_BUS_DISPATCH_OPS

    assert frozenset(AGENT_BUS_OPS) == AGENT_BUS_DISPATCH_OPS


def test_surface_enum_proxy_injects_agent_bus_ops() -> None:
    from surface_enum import _SurfaceEnumToolProxy

    class _StubTool:
        name = "agent_bus"

        def to_mcp_tool(self, **_: object) -> object:
            return type(
                "_McpTool",
                (),
                {
                    "inputSchema": {
                        "properties": {
                            "tool": {"type": "string"},
                            "arguments": {"type": "string"},
                        }
                    }
                },
            )()

    proxy = _SurfaceEnumToolProxy(_StubTool(), advertised_agent_bus_ops())
    mcp_tool = proxy.to_mcp_tool()
    tool_prop = mcp_tool.inputSchema["properties"]["tool"]
    assert "reply" not in tool_prop["enum"]
    assert "post" not in tool_prop["enum"]
    assert "send" in tool_prop["enum"]
    assert "request" in tool_prop["enum"]
    assert "hop" in tool_prop["enum"]
    assert "wait" in tool_prop["enum"]
