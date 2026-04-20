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
from agent_seat.executor import _parse_dispatch_arguments, execute_tool


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
    assert json.loads(result) == {"error": "Unknown tool: nonsense"}


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
        "agent": "oppie",
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
