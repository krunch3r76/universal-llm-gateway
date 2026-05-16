"""In-process tests for ToolErrorEnricher using fastmcp.client.Client against tiny FastMCP.

Covers unexpected_keyword_argument (accepted/required/hint), type-mismatch (expected_type), and happy-path pass-through.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from fastmcp import Client, FastMCP
from tool_error_enricher import register_tool_error_enricher


@pytest.mark.asyncio
async def test_unexpected_keyword_argument_enrichment():
    mcp = FastMCP("test-enricher")
    @mcp.tool()
    def fs(op: str, path: str = "") -> dict:
        return {"op": op, "path": path}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool("fs", {"op": "read", "view_range": [1, 60]})
        env = res.structured_content
        assert env["error_type"] == "ValidationError"
        assert env["tool"] == "fs"
        err0 = env["errors"][0]
        assert err0["type"] == "unexpected_keyword_argument"
        assert err0["param"] == "view_range"
        assert "accepted_params" in err0
        assert "required_params" in err0
        assert "hint" in err0
        assert "view_range" in err0["hint"]
        assert "not a parameter" in err0["hint"]


@pytest.mark.asyncio
async def test_type_mismatch_enrichment_includes_expected_type():
    mcp = FastMCP("test-enricher")
    @mcp.tool()
    def sample(a: int, b: str = "x") -> dict:
        return {"a": a, "b": b}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool("sample", {"a": "not-a-number", "b": "ok"})
        env = res.structured_content
        assert env["error_type"] == "ValidationError"
        err0 = env["errors"][0]
        assert err0["type"] == "int_parsing"
        assert err0["param"] == "a"
        assert err0.get("expected_type") == "integer"
        assert "hint" in err0
        assert err0["input_type"] == "str"


@pytest.mark.asyncio
async def test_valid_call_passes_through_unchanged():
    mcp = FastMCP("test-enricher")
    @mcp.tool()
    def sample(a: int) -> dict:
        return {"got": a * 2}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool("sample", {"a": 21})
        assert res.structured_content == {"got": 42}
        assert getattr(res, "is_error", False) is False
