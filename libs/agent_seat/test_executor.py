"""Tests for agent_seat async tool executor.

Covers argument parsing + op-table dispatch. Actual network calls to Cortex
and agent-bus are mocked via monkey-patching the internal request helpers —
the goal is to verify method + path + body construction, not to re-test the
REST services.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

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
async def test_execute_unknown_tool_returns_error() -> None:
    result = await execute_tool("nonsense", {})
    data = json.loads(result)
    assert data["code"] == "client_side_mcp_http_refused"


@pytest.mark.asyncio
async def test_execute_mcp_server_tool_refuses_http_path() -> None:
    result = await execute_tool("web_search", {"query": "test"})
    data = json.loads(result)
    assert data["code"] == "client_side_mcp_http_refused"
    assert "remote_mcp" in data["error"]


@pytest.mark.asyncio
async def test_brave_search_refuses_without_remote_mcp() -> None:
    result = await execute_tool("brave_search", {"query": "eth price"})
    data = json.loads(result)
    assert data["code"] == "client_side_mcp_http_refused"


@pytest.mark.asyncio
async def test_cortex_dispatch_sends_agent_seat_caller_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"ok": True}

    async def fake_post(path: str, **kwargs: Any) -> _Resp:
        captured["path"] = path
        captured.update(kwargs)
        return _Resp()

    client_cm = MagicMock()
    client_cm.__aenter__.return_value.post = fake_post
    client_cm.__aexit__.return_value = None

    monkeypatch.setattr(_ex, "make_async_client", lambda *a, **k: client_cm)

    await _ex._cortex_dispatch("stats", {})
    assert captured["headers"]["X-ULG-Caller"] == "agent_seat"
    assert captured["json"]["surface"] == "code"
    assert "via_adapter" not in captured["json"]


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
async def test_execute_agent_bus_reply_injects_active_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_seat.context import bind_active_role, reset_active_role

    captured: dict[str, Any] = {}

    async def fake_request(method: str, path: str, body: dict | None = None) -> dict:
        captured.update({"method": method, "path": path, "body": body})
        return {"turn_number": 6}

    monkeypatch.setattr(_ex, "_agent_bus_request", fake_request)

    token = bind_active_role("gatherer")
    try:
        await execute_tool(
            "agent_bus",
            {
                "tool": "reply",
                "arguments": json.dumps(
                    {
                        "thread": "480",
                        "to": "cursor",
                        "subject": "sub",
                        "body": "content",
                        "after_turn": 4,
                    }
                ),
            },
        )
    finally:
        reset_active_role(token)

    assert captured["body"] is not None
    assert captured["body"]["from"] == "gatherer"


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
async def test_get_mcp_tool_definitions_returns_in_process_registry() -> None:
    defs = await get_mcp_tool_definitions()
    names = [d["function"]["name"] for d in defs]
    assert names == ["cortex", "agent_bus"]


@pytest.mark.asyncio
async def test_get_mcp_tool_definitions_does_not_http_dial_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    async def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("httpx must not be called for in-process tool defs")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    defs = await get_mcp_tool_definitions()
    assert defs


@pytest.mark.asyncio
async def test_resolve_tool_definitions_in_process_only() -> None:
    defs = await resolve_tool_definitions(["cortex", "agent_bus"])
    assert [d["function"]["name"] for d in defs] == ["cortex", "agent_bus"]


@pytest.mark.asyncio
async def test_resolve_tool_definitions_rejects_mcp_only_names() -> None:
    with pytest.raises(ValueError, match="MCP-only"):
        await resolve_tool_definitions(["rag"])
