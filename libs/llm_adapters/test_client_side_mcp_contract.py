"""Client-side MCP tool-loop contract (C2)."""

from __future__ import annotations

import pytest

from llm_adapters.client_side_mcp import (
    client_side_mcp_tool_loop_allowed,
    validate_client_side_mcp_tool_loop,
)
from llm_adapters.core import FrontierRequest


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object", "properties": {}}},
    }


def test_client_side_loop_allowed_for_in_process_tools_only() -> None:
    tools = [_tool("cortex"), _tool("agent_bus")]
    assert client_side_mcp_tool_loop_allowed(tools=tools, remote_mcp=False) is True


def test_client_side_loop_disallowed_with_mcp_server_tool_names() -> None:
    tools = [_tool("cortex"), _tool("rag")]
    assert client_side_mcp_tool_loop_allowed(tools=tools, remote_mcp=False) is False


def test_validate_raises_when_loop_would_dial_mcp() -> None:
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="openai/gpt-5.4",
        tools=[_tool("fs")],
        mcp_tool_loop=True,
        remote_mcp=False,
    )
    with pytest.raises(ValueError, match="remote_mcp"):
        validate_client_side_mcp_tool_loop(req)


def test_validate_allows_remote_mcp_path_unchanged() -> None:
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="openai/gpt-5.4",
        tools=[{"type": "mcp", "server_label": "vortex", "server_url": "https://x"}],
        mcp_tool_loop=False,
        remote_mcp=True,
    )
    validate_client_side_mcp_tool_loop(req)
