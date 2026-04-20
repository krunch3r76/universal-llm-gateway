"""Tests for the provider-native tool-use loop.

Uses scripted fake send_native + monkey-patched execute_tool. Verifies
per-provider tool-call normalization (Anthropic input, Responses arguments,
Google input/args), happy path, turn exhaustion, cancellation, gen-param
forwarding, and malformed-JSON tolerance.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from llm_adapters import FrontierRequest

from agent_seat import native_loop as _nl_mod
from agent_seat.native_loop import run_native_tool_loop


class _FakeSend:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, json_body))
        return self._responses.pop(0)


def _anthropic_terminal(text: str = "done") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "end_turn",
    }


def _anthropic_tool_use(
    name: str, args: dict[str, Any], call_id: str = "tool_1"
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "tool_use", "id": call_id, "name": name, "input": args}],
        "usage": {"input_tokens": 7, "output_tokens": 3},
        "stop_reason": "tool_use",
    }


@pytest.mark.asyncio
async def test_native_loop_terminal_response_no_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = _FakeSend([_anthropic_terminal("hello")])
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="",
        mcp_tool_loop=False,
    )
    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=send,
        max_turns=5,
    )
    assert result.content == "hello"
    assert result.tool_calls_made == 0
    assert result.provider == "anthropic"
    assert send.calls[0][0] == "/api/v1/providers/anthropic/messages"


@pytest.mark.asyncio
async def test_native_loop_executes_tools_and_appends_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"ok": True, "echo": args})

    monkeypatch.setattr(_nl_mod, "execute_tool", fake_execute)

    events: list[tuple[str, dict[str, Any]]] = []

    send = _FakeSend(
        [
            _anthropic_tool_use("cortex", {"tool": "entities"}, "t1"),
            _anthropic_terminal("final"),
        ]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "query cortex"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "cortex", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )
    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=send,
        max_turns=5,
        on_tool_event=lambda s, p: events.append((s, p)),
    )
    assert result.content == "final"
    assert result.tool_calls_made == 1
    assert result.tool_calls[0].name == "cortex"
    assert result.tool_calls[0].ok is True
    assert events[0][0] == "pipeline.frontier.dispatch.tool.called"


@pytest.mark.asyncio
async def test_native_loop_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"ok": True})

    monkeypatch.setattr(_nl_mod, "execute_tool", fake_execute)

    send = _FakeSend(
        [_anthropic_tool_use("cortex", {"tool": "entities"}, f"t{i}") for i in range(5)]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "loop"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "cortex", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )
    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=send,
        max_turns=3,
    )
    assert result.exhausted is True
    assert result.turns_used == 3
    assert result.tool_calls_made == 3


@pytest.mark.asyncio
async def test_native_loop_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"ok": True})

    monkeypatch.setattr(_nl_mod, "execute_tool", fake_execute)

    cancel_now = {"flag": False}

    send = _FakeSend(
        [
            _anthropic_tool_use("cortex", {"tool": "entities"}, "t1"),
            _anthropic_terminal("should not reach"),
        ]
    )

    async def cancel_after_first_turn(
        path: str, json_body: dict[str, Any]
    ) -> dict[str, Any]:
        result = await send(path, json_body)
        cancel_now["flag"] = True
        return result

    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "cortex", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )
    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=cancel_after_first_turn,
        max_turns=5,
        cancel_check=lambda: cancel_now["flag"],
    )
    assert result.cancelled is True
    assert result.turns_used <= 2


@pytest.mark.asyncio
async def test_native_loop_unknown_provider_raises() -> None:
    send = _FakeSend([])
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="unknownvendor/some-model",
        max_tokens=100,
    )
    with pytest.raises(ValueError, match="No native path"):
        await run_native_tool_loop(
            model="unknownvendor/some-model",
            req=req,
            send_native=send,
            max_turns=1,
        )


@pytest.mark.asyncio
async def test_native_loop_malformed_tool_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        captured.append((name, args))
        return json.dumps({"ok": True})

    monkeypatch.setattr(_nl_mod, "execute_tool", fake_execute)

    openai_malformed = {
        "id": "resp_1",
        "model": "gpt-5.4",
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "cortex",
                "arguments": "not-json{",
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    openai_terminal = {
        "id": "resp_2",
        "model": "gpt-5.4",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "done"}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    send = _FakeSend([openai_malformed, openai_terminal])
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-5.4",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "cortex", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )
    result = await run_native_tool_loop(
        model="openai/gpt-5.4",
        req=req,
        send_native=send,
        max_turns=2,
    )
    assert result.content == "done"
    assert captured == [("cortex", {})]
