"""Tests for response_size_guard tape exemption (AMEND-R T17)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams

from response_size_guard import ResponseSizeGuard, _is_tape_read_exempt

pytestmark = pytest.mark.offline


def _context(tool_name: str, arguments: dict) -> MagicMock:
    ctx = MagicMock()
    ctx.message = CallToolRequestParams(name=tool_name, arguments=arguments)
    return ctx


def test_t17_tape_read_exempt_detected() -> None:
    assert _is_tape_read_exempt(_context("agent_bus_read", {"tool": "tape", "arguments": "{}"}))
    assert not _is_tape_read_exempt(_context("agent_bus_read", {"tool": "fetch", "arguments": "{}"}))


@pytest.mark.asyncio
async def test_t17_large_tape_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_CURSOR_RESPONSE_SIZE_LIMIT", "1024")
    huge = {"open_line": {"payload_bytes": 1_000_000}, "messages": ["x" * 500_000]}
    result = ToolResult(structured_content=huge)
    call_next = AsyncMock(return_value=result)
    guard = ResponseSizeGuard()
    out = await guard.on_call_tool(
        _context("agent_bus_read", {"tool": "tape", "arguments": '{"thread":"1"}'}),
        call_next,
    )
    assert out is result
    call_next.assert_awaited_once()
