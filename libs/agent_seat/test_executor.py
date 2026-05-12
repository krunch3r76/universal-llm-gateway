"""Tests for agent_seat async tool executor.

Covers argument parsing + op-table dispatch. Actual network calls to Cortex
and agent-bus are mocked via monkey-patching the internal request helpers —
the goal is to verify method + path + body construction, not to re-test the
REST services.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_seat import executor as _ex
from agent_seat.executor import (
    _parse_dispatch_arguments,
    execute_tool,
    get_mcp_tool_definitions,
    resolve_tool_definitions,
)


def test_parse_dispatch_arguments_dict_passthrough() -> None:
    assert _parse_dispatch_arguments({"a": 1}) == {"a": 1}


def test_parse_dispatch_arguments_json_string() -> None:
    assert _parse_dispatch_arguments('{"b": 2}') == {"b": 2}


def test_parse_dispatch_arguments_malformed_returns_none() -> None:
    assert _parse_dispatch_arguments("not-json") is None
    assert _parse_dispatch_arguments(42) is None
    assert _parse_dispatch_arguments(None) is None


@pytest.mark.asyncio
async def test_execute_unknown_tool_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR_INITIALIZED", True)
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR", None)
    result = await execute_tool("nonsense", {})
    assert json.loads(result) == {"error": "Unknown tool: nonsense"}


@pytest.mark.asyncio
async def test_execute_unknown_tool_uses_mcp_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeMcpExecutor:
        async def execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
            return json.dumps({"name": name, "arguments": arguments, "source": "mcp"})

    monkeypatch.setattr(_ex, "_MCP_EXECUTOR_INITIALIZED", True)
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR", _FakeMcpExecutor())

    result = await execute_tool("web_search", {"query": "test"})
    assert json.loads(result) == {
        "name": "web_search",
        "arguments": {"query": "test"},
        "source": "mcp",
    }


@pytest.mark.asyncio
async def test_brave_search_alias_remaps_to_mcp_web_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brave_search must invoke MCP with name='web_search', not 'brave_search'."""

    class _FakeMcpExecutor:
        async def execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
            return json.dumps({"name": name, "arguments": arguments, "source": "mcp"})

    monkeypatch.setattr(_ex, "_MCP_EXECUTOR_INITIALIZED", True)
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR", _FakeMcpExecutor())

    result = await execute_tool("brave_search", {"query": "eth price"})
    data = json.loads(result)
    assert data["name"] == "web_search", (
        "brave_search must remap to MCP 'web_search'; got "
        f"{data['name']!r}. Native model tools would shadow 'brave_search' "
        "if it were passed as-is to the MCP server."
    )
    assert data["arguments"] == {"query": "eth price"}


@pytest.mark.asyncio
async def test_cortex_dispatch_entity_get_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_dispatch(tool: str, arguments: dict[str, Any]) -> dict:
        captured.update({"tool": tool, "arguments": arguments})
        return {"entity": "ok"}

    monkeypatch.setattr(_ex, "_cortex_dispatch", fake_dispatch)

    result = await execute_tool(
        "cortex",
        {"tool": "entity_get", "arguments": {"entity_id": "person:jane-doe"}},
    )
    assert captured["tool"] == "entity_get"
    assert captured["arguments"] == {"entity_id": "person:jane-doe"}
    assert json.loads(result) == {"entity": "ok"}


@pytest.mark.asyncio
async def test_execute_cortex_dispatch_entities_forwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_dispatch(tool: str, arguments: dict[str, Any]) -> dict:
        captured.update({"tool": tool, "arguments": arguments})
        return {"items": []}

    monkeypatch.setattr(_ex, "_cortex_dispatch", fake_dispatch)

    result = await execute_tool(
        "cortex",
        {"tool": "entities", "arguments": '{"type": "todo", "limit": 5}'},
    )
    assert captured["tool"] == "entities"
    assert captured["arguments"] == {"type": "todo", "limit": 5}
    assert json.loads(result) == {"items": []}


@pytest.mark.asyncio
async def test_execute_cortex_dispatch_assert_forwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_dispatch(tool: str, arguments: dict[str, Any]) -> dict:
        captured.update({"tool": tool, "arguments": arguments})
        return {"id": 42}

    monkeypatch.setattr(_ex, "_cortex_dispatch", fake_dispatch)

    args = {
        "entity_id": "decision:x",
        "claim": "hello",
        "confidence": "believed",
        "agent": "skeptic",
    }
    await execute_tool(
        "cortex",
        {"tool": "assert", "arguments": json.dumps(args)},
    )
    assert captured["tool"] == "assert"
    assert captured["arguments"] == args


@pytest.mark.asyncio
async def test_execute_cortex_dispatch_unknown_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_dispatch(tool: str, arguments: dict[str, Any]) -> dict:
        return {
            "error": (
                f"Unknown cortex tool {tool!r}. Available: ['assert', 'entities']"
            ),
        }

    monkeypatch.setattr(_ex, "_cortex_dispatch", fake_dispatch)

    result = await execute_tool(
        "cortex",
        {"tool": "nonsense_op", "arguments": "{}"},
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "nonsense_op" in parsed["error"]


@pytest.mark.asyncio
async def test_execute_cortex_dispatch_invalid_arguments() -> None:
    result = await execute_tool(
        "cortex",
        {"tool": "entities", "arguments": "not-json{"},
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "Invalid arguments JSON" in parsed["error"]


@pytest.mark.asyncio
async def test_execute_agent_bus_fetch_builds_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_request(method: str, path: str, body: dict | None = None) -> dict:
        captured.update({"method": method, "path": path, "body": body})
        return {"turns": []}

    monkeypatch.setattr(_ex, "_agent_bus_request", fake_request)

    await execute_tool(
        "agent_bus",
        {"tool": "fetch", "arguments": '{"thread": "480", "last": 3, "compact": true}'},
    )
    assert captured["method"] == "GET"
    assert "thread=480" in captured["path"]
    assert "last=3" in captured["path"]
    assert captured["body"] is None


@pytest.mark.asyncio
async def test_execute_agent_bus_reply_posts_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_request(method: str, path: str, body: dict | None = None) -> dict:
        captured.update({"method": method, "path": path, "body": body})
        return {"turn_number": 5}

    monkeypatch.setattr(_ex, "_agent_bus_request", fake_request)

    args = {
        "thread": "480",
        "to": "cursor",
        "subject": "sub",
        "body": "content",
        "after_turn": 4,
    }
    await execute_tool(
        "agent_bus",
        {"tool": "reply", "arguments": json.dumps(args)},
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/turns"
    assert captured["body"] == args


@pytest.mark.asyncio
async def test_get_mcp_tool_definitions_returns_live_defs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeMcpExecutor:
        def get_openai_tool_defs(self) -> list[dict[str, Any]]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

    monkeypatch.setattr(_ex, "_MCP_EXECUTOR_INITIALIZED", True)
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR", _FakeMcpExecutor())

    defs = await get_mcp_tool_definitions()
    assert defs[0]["function"]["name"] == "web_search"


@pytest.mark.asyncio
async def test_get_mcp_executor_retries_after_missing_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR_INITIALIZED", False)
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR", None)
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)

    executor = await _ex._get_mcp_executor()

    assert executor is None
    assert _ex._MCP_EXECUTOR_INITIALIZED is False


@pytest.mark.asyncio
async def test_resolve_tool_definitions_combines_static_and_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeMcpExecutor:
        def get_openai_tool_defs(self) -> list[dict[str, Any]]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

    monkeypatch.setattr(_ex, "_MCP_EXECUTOR_INITIALIZED", True)
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR", _FakeMcpExecutor())

    defs = await resolve_tool_definitions(["cortex", "web_search"])
    assert [d["function"]["name"] for d in defs] == ["cortex", "web_search"]


@pytest.mark.asyncio
async def test_resolve_tool_definitions_uses_live_rag_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeMcpExecutor:
        def get_openai_tool_defs(self) -> list[dict[str, Any]]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "rag",
                        "description": "RAG dispatch",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

    monkeypatch.setattr(_ex, "_MCP_EXECUTOR_INITIALIZED", True)
    monkeypatch.setattr(_ex, "_MCP_EXECUTOR", _FakeMcpExecutor())

    defs = await resolve_tool_definitions(["rag"])
    assert [d["function"]["name"] for d in defs] == ["rag"]
