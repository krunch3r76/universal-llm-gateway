"""Block sql in observability preview (cursor_safe)."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from tools.events import register_event_tools


def test_preview_blocks_sql_like_raw_sql() -> None:
    mcp = FastMCP("test-events-preview")
    register_event_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    preview = next(t for t in tools if t.name == "query_observability_preview").fn
    result = preview(operation="sql", params={"sql": "SELECT 1"})
    assert result == {"error": "Operation 'sql' is not allowed in preview mode."}
