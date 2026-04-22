from __future__ import annotations

import json

import httpx
import pytest

from services.universal_cloud_proxy.mcp_executor import (
    McpToolExecutor,
    _compat_dispatch_tool_defs,
    _mcp_schema_to_openai_tool,
)


def test_mcp_schema_to_openai_tool_sanitizes_function_schema() -> None:
    tool = {
        "name": "web_fetch",
        "description": "Fetch a URL",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "title": "Url"},
                "headers": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {"Authorization": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        {"type": "null"},
                    ],
                    "default": None,
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    }

    out = _mcp_schema_to_openai_tool(tool)
    params = out["function"]["parameters"]

    assert out["function"]["name"] == "web_fetch"
    assert params["type"] == "object"
    assert "additionalProperties" not in params
    assert "title" not in params["properties"]["url"]
    headers = params["properties"]["headers"]
    assert headers["type"] == "object"
    assert "additionalProperties" not in headers
    assert "default" not in headers


def test_compat_dispatch_tool_defs_restores_web_fetch() -> None:
    defs = _compat_dispatch_tool_defs({"web_search", "dispatch"})
    assert [d["function"]["name"] for d in defs] == ["web_fetch"]


@pytest.mark.asyncio
async def test_execute_tool_routes_hidden_web_fetch_via_dispatch() -> None:
    calls: list[dict[str, object]] = []

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            calls.append(
                {
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text='{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"ok"}]}}',
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]
    executor._dispatch_compat_names = {"web_fetch"}

    result = await executor.execute_tool("web_fetch", {"url": "https://example.com"})

    assert result == "ok"
    assert calls
    params = calls[0]["json"]["params"]  # type: ignore[index]
    assert params["name"] == "dispatch"
    assert params["arguments"] == {
        "tool": "web_fetch",
        "arguments": {"url": "https://example.com"},
    }
