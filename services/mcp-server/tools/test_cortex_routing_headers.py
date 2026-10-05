"""Cortex MCP tool sends routing headers and still sends body fields."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from fastmcp import FastMCP
from request_profile import bind_request

from tools.cortex import register_cortex_tools


def test_cortex_tool_sends_adapter_header_and_body_fields() -> None:
    mcp = FastMCP("test-cortex-routing-headers")
    register_cortex_tools(mcp, surface="code")
    tools = asyncio.run(mcp.list_tools())
    cortex_tool = next(t for t in tools if t.name == "cortex")
    with (
        bind_request(
            "default",
            surface="code",
            seat_class="cursor-sdk",
            caller_identity="static",
            mcp_session_id="sess-1",
        ),
        patch("tools.cortex.cx", return_value={"ok": True}) as cx,
    ):
        result = cortex_tool.fn(tool="stats", arguments="{}")
    assert result == {"ok": True}
    kwargs = cx.call_args.kwargs
    assert kwargs["headers"]["X-ULG-Adapter"] == "mcp-server"
    assert kwargs["headers"]["X-ULG-Surface"] == "code"
    assert kwargs["headers"]["X-ULG-Seat"] == "cursor-sdk"
    assert kwargs["headers"]["X-ULG-Caller"] == "static"
    assert kwargs["headers"]["X-ULG-Session"] == "sess-1"
    body = cx.call_args.args[2]
    assert body["surface"] == "code"
    assert body["seat"] == "cursor-sdk"
    assert body["via_adapter"] is True
    assert body["tool"] == "stats"
