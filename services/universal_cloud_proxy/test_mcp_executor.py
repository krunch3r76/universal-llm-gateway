from __future__ import annotations

import json

import httpx
import pytest

from services.universal_cloud_proxy import mcp_executor as mcp_executor_module
from services.universal_cloud_proxy.boot_directive import parse_boot_directive
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


def test_mcp_schema_to_openai_tool_drops_const_and_single_value_enum() -> None:
    tool = {
        "name": "cortex",
        "description": "Cortex knowledge",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "const": "entities",
                    "enum": ["entities"],
                },
            },
            "required": ["tool"],
        },
    }

    params = _mcp_schema_to_openai_tool(tool)["function"]["parameters"]
    tool_prop = params["properties"]["tool"]

    assert "const" not in tool_prop
    assert "enum" not in tool_prop


def test_compat_dispatch_tool_defs_restores_web_fetch() -> None:
    defs = _compat_dispatch_tool_defs({"web_search", "dispatch"})
    assert [d["function"]["name"] for d in defs] == ["web_fetch"]


def test_parse_boot_directive_non_match() -> None:
    assert parse_boot_directive("hello world") is None
    assert parse_boot_directive('cortex_boot(transcript_id="foo")') is None


@pytest.mark.asyncio
async def test_resolve_boot_directive_agent_claude_cursor() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "BRIEFING"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    messages = [
        {
            "role": "system",
            "content": 'Start with cortex_boot(agent="claude-cursor") then work.',
        }
    ]
    await executor._resolve_boot_directive(messages)

    assert calls == [("cortex_boot", {"agent": "claude-cursor"})]
    assert messages[0]["content"] == "Start with BRIEFING then work."


@pytest.mark.asyncio
async def test_resolve_boot_directive_family_platform() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "API MULTI"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    messages = [
        {
            "role": "system",
            "content": 'cortex_boot(family="Grok", platform="api-multi")',
        }
    ]
    await executor._resolve_boot_directive(messages)

    assert calls == [
        ("cortex_boot", {"family": "Grok", "platform": "api-multi"}),
    ]
    assert messages[0]["content"] == "API MULTI"


@pytest.mark.asyncio
async def test_resolve_boot_directive_passes_all_primary_params() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "CARD"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    messages = [
        {
            "role": "system",
            "content": (
                'cortex_boot(agent="claude-web", family="grok", '
                'platform="cursor", role="lead")'
            ),
        }
    ]
    await executor._resolve_boot_directive(messages)

    assert calls == [
        (
            "cortex_boot",
            {
                "agent": "claude-web",
                "family": "grok",
                "platform": "cursor",
                "role": "lead",
            },
        )
    ]


@pytest.mark.asyncio
async def test_resolve_boot_directive_leaves_unmatched_prompt() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "CARD"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    original = "No boot directive here."
    messages = [{"role": "system", "content": original}]
    await executor._resolve_boot_directive(messages)

    assert calls == []
    assert messages[0]["content"] == original


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
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"ok"}]}}'
                ),
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


@pytest.mark.asyncio
async def test_execute_tool_retries_remote_protocol_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    monkeypatch.setattr(mcp_executor_module, "_RESTART_RETRY_DELAYS_S", (0.0,))

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.RemoteProtocolError("server closed connection")
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"ok"}]}}'
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_execute_tool_retries_on_503_restart_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 with restart payload triggers retry; second attempt succeeds."""
    calls = 0
    monkeypatch.setattr(mcp_executor_module, "_RESTART_RETRY_DELAYS_S", (0.0,))

    restart_body = (
        '{"jsonrpc":"2.0","id":1,'
        '"error":{"code":-32099,"message":"MCP server is restarting; retry in 30s",'
        '"data":{"reason":"server_restarting","retry_after_s":30}}}'
    )

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            req = httpx.Request("POST", "https://mcp.example.com/mcp")
            if calls == 1:
                return httpx.Response(503, request=req, text=restart_body)
            return httpx.Response(
                200,
                request=req,
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"ok"}]}}'
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_execute_tool_does_not_retry_generic_503() -> None:
    """503 without restart payload is a real error and must NOT trigger retry."""
    calls = 0

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                503,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text='{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"capacity exceeded"}}',
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert calls == 1
    assert "Tool execution failed" in result or "restart" not in result


@pytest.mark.asyncio
async def test_execute_tool_retries_on_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConnectError during the container-down window must trigger retry."""
    calls = 0
    monkeypatch.setattr(mcp_executor_module, "_RESTART_RETRY_DELAYS_S", (0.0,))

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"ok"}]}}'
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert result == "ok"
    assert calls == 2
