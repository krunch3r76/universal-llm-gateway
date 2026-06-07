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
from agent_seat import native_loop_tools as _nl_tools_mod
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

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

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

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

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
async def test_native_loop_stops_on_repeated_section_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        assert name == "fs"
        assert args["op"] == "md_read"
        return json.dumps({"error": "Section not found: Missing Section"})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

    send = _FakeSend(
        [
            _anthropic_tool_use(
                "fs",
                {
                    "op": "md_read",
                    "path": "docs/example.md",
                    "section": "Missing Section",
                },
                "t1",
            ),
            _anthropic_tool_use(
                "fs",
                {
                    "op": "md_read",
                    "path": "docs/example.md",
                    "section": "Missing Section",
                },
                "t2",
            ),
            _anthropic_terminal("should not reach"),
        ]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "read section"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "fs", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )

    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=send,
        max_turns=5,
    )

    assert result.exhausted is True
    assert result.turns_used == 2
    assert result.tool_calls_made == 2
    assert result.exhaustion_summary is not None
    assert result.exhaustion_summary["exhaustion_reason"].startswith(
        "repeated_section_not_found"
    )
    assert result.exhaustion_summary["failed_tools"][0]["tool"] == "fs.md_read"


@pytest.mark.asyncio
async def test_native_loop_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"ok": True})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

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
    with pytest.raises(ValueError):
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

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

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


# ---------------------------------------------------------------------------
# Synthesis-round tests — exhaustion recovery via append_tool_round +
# strip_tools + append_exhaustion_advisory. See decision:cortex-tool-loop-
# exhaustion-synthesis-round and agent-bus thread 1015 for the architecture.
# ---------------------------------------------------------------------------


def _anthropic_repeated_section_miss_round(call_id: str) -> dict[str, Any]:
    """Anthropic ``tool_use`` round that targets the same fs.md_read miss
    pattern used by ``test_native_loop_stops_on_repeated_section_miss`` to
    trip ``ToolFrictionTracker.should_stop`` on the second occurrence.
    """
    return _anthropic_tool_use(
        "fs",
        {
            "op": "md_read",
            "path": "docs/example.md",
            "section": "Missing Section",
        },
        call_id,
    )


@pytest.mark.asyncio
async def test_synthesis_round_fires_after_friction_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When friction halts the loop, the synthesis round should:
    1. Append the pending tool round so the model sees what just happened
    2. Strip the tool inventory
    3. Append the exhaustion advisory
    4. Send one more request (NOT counted against max_turns)
    5. Use the synth content as the final result, with synthesized=True
    """

    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"error": "Section not found: Missing Section"})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

    # Two repeated misses trip friction.should_stop. Then the synth round
    # is fired with NO tools available — the model produces final text.
    send = _FakeSend(
        [
            _anthropic_repeated_section_miss_round("t1"),
            _anthropic_repeated_section_miss_round("t2"),
            _anthropic_terminal(
                "Could not find the section; here is what I can say from context."
            ),
        ]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "find the missing section"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "fs", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )

    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=send,
        max_turns=5,
    )

    assert result.exhausted is True
    assert result.synthesized is True
    assert result.content.startswith("Could not find the section")
    assert result.turns_used == 2  # synth round does NOT count
    assert result.tool_calls_made == 2

    # The synth request body must NOT carry a tools inventory and MUST
    # carry the exhaustion advisory.
    synth_call_body = send.calls[-1][1]
    assert "tools" not in synth_call_body or synth_call_body["tools"] == []
    assert "tool_choice" not in synth_call_body
    last_msg = synth_call_body["messages"][-1]
    assert last_msg["role"] == "user"
    assert isinstance(last_msg["content"], str)
    assert "Tool budget exhausted" in last_msg["content"]


@pytest.mark.asyncio
async def test_synthesis_round_fires_after_max_turns_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When max_turns runs out, the synthesis round should still fire and
    NOT need to append a pending round (the loop's last iteration already
    called append_tool_round before the for-else clause).
    """

    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"ok": True})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

    # Three turns of fresh tool calls (distinct args avoid friction halt),
    # then the synth round produces final text.
    send = _FakeSend(
        [
            _anthropic_tool_use("cortex", {"tool": "entities", "n": i}, f"t{i}")
            for i in range(3)
        ]
        + [_anthropic_terminal("here is the partial summary I could assemble.")]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "look around"}],
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
    assert result.synthesized is True
    assert result.content.startswith("here is the partial summary")
    assert result.turns_used == 3
    assert result.tool_calls_made == 3

    # Synth body has the advisory, no tools.
    synth_call_body = send.calls[-1][1]
    assert "tools" not in synth_call_body or synth_call_body["tools"] == []
    last_msg = synth_call_body["messages"][-1]
    assert "Tool budget exhausted" in last_msg["content"]


@pytest.mark.asyncio
async def test_synthesis_round_empty_content_keeps_exhausted_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the synth round returns empty content, we fall back to the prior
    exhausted-empty behavior — exhausted stays True, content="",
    synthesized stays False (we never replaced result with the synth).
    """

    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"error": "Section not found: Missing Section"})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

    empty_terminal = {
        "id": "msg_synth",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [],  # empty — no text block
        "usage": {"input_tokens": 1, "output_tokens": 0},
        "stop_reason": "end_turn",
    }

    send = _FakeSend(
        [
            _anthropic_repeated_section_miss_round("t1"),
            _anthropic_repeated_section_miss_round("t2"),
            empty_terminal,
        ]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "find it"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "fs", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )

    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=send,
        max_turns=5,
    )

    assert result.exhausted is True
    assert result.synthesized is False
    assert result.content == ""
    assert result.tool_calls_made == 2


@pytest.mark.asyncio
async def test_synthesis_round_skipped_when_adapter_lacks_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the resolved adapter is missing one of the synthesis hooks, the
    loop must still terminate cleanly with the prior exhausted-empty
    behavior. (Defensive — should not happen with the current cloud
    adapter set, but new/test adapters might omit the hooks.)
    """

    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"error": "Section not found: Missing Section"})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

    # Patch the adapter resolver to return an object missing strip_tools.
    real_resolve = _nl_mod.resolve_llm_adapter

    class _AdapterWithoutSynth:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def build_frontier_request(self, req: Any) -> Any:
            return self._inner.build_frontier_request(req)

        def parse_frontier_response(self, raw: Any) -> Any:
            return self._inner.parse_frontier_response(raw)

        def append_tool_round(self, body: Any, raw: Any, tool_results: Any) -> None:
            self._inner.append_tool_round(body, raw, tool_results)

    def fake_resolve(provider: Any) -> Any:
        real = real_resolve(provider)
        return _AdapterWithoutSynth(real) if real is not None else None

    monkeypatch.setattr(_nl_mod, "resolve_llm_adapter", fake_resolve)

    send = _FakeSend(
        [
            _anthropic_repeated_section_miss_round("t1"),
            _anthropic_repeated_section_miss_round("t2"),
        ]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "find it"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "fs", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )

    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=send,
        max_turns=5,
    )

    assert result.exhausted is True
    assert result.synthesized is False
    assert result.content == ""
    # Only the two tool-call turns were sent; no synth attempt.
    assert len(send.calls) == 2


@pytest.mark.asyncio
async def test_synthesis_round_does_not_fire_on_clean_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the loop terminates because the model produced terminal content
    (no exhaustion), the synth round must NOT fire — synthesized stays
    False and only the in-loop turns are sent.
    """

    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"ok": True})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

    send = _FakeSend(
        [
            _anthropic_tool_use("cortex", {"tool": "entities"}, "t1"),
            _anthropic_terminal("done"),
        ]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "look"}],
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
    )

    assert result.exhausted is False
    assert result.synthesized is False
    assert result.content == "done"
    assert len(send.calls) == 2


@pytest.mark.asyncio
async def test_synthesis_round_swallows_send_native_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the synth round's send_native call raises, we fall back to the
    prior exhausted-empty behavior. The whole loop must NOT bubble the
    synth-only failure to the caller.
    """

    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"error": "Section not found: Missing Section"})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

    class _RaisingSend:
        def __init__(self, tool_responses: list[dict[str, Any]]) -> None:
            self._responses = list(tool_responses)
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def __call__(
            self, path: str, json_body: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append((path, json_body))
            if not self._responses:
                raise RuntimeError("synth round network failure")
            return self._responses.pop(0)

    send = _RaisingSend(
        [
            _anthropic_repeated_section_miss_round("t1"),
            _anthropic_repeated_section_miss_round("t2"),
        ]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "find it"}],
        model="claude-sonnet-4-6",
        max_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "fs", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )

    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-4-6",
        req=req,
        send_native=send,
        max_turns=5,
    )

    assert result.exhausted is True
    assert result.synthesized is False
    assert result.content == ""
    # Two tool turns + one failed synth attempt.
    assert len(send.calls) == 3


# ---------------------------------------------------------------------------
# CP-001 — usage accumulation across all provider turns
# ---------------------------------------------------------------------------


def test_accumulate_usage_sums_basic_tokens() -> None:
    """accumulate_usage sums input/output across multiple turns."""
    from agent_seat.native_loop_tools import accumulate_usage

    acc: dict = {}
    accumulate_usage(acc, {"input_tokens": 10, "output_tokens": 5})
    accumulate_usage(acc, {"input_tokens": 20, "output_tokens": 8})
    accumulate_usage(acc, {"input_tokens": 15, "output_tokens": 3})
    assert acc["input_tokens"] == 45
    assert acc["output_tokens"] == 16


def test_accumulate_usage_none_semantics_preserved() -> None:
    """cached_tokens / reasoning_tokens stay absent until a turn reports them."""
    from agent_seat.native_loop_tools import accumulate_usage

    acc: dict = {}
    # First two turns: no cached/reasoning values
    accumulate_usage(
        acc,
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "reasoning_tokens": None,
            "cached_tokens": None,
        },
    )
    accumulate_usage(acc, {"input_tokens": 20, "output_tokens": 8})
    assert "cached_tokens" not in acc or acc.get("cached_tokens") is None
    assert "reasoning_tokens" not in acc or acc.get("reasoning_tokens") is None

    # Third turn reports reasoning_tokens — now it becomes a number
    accumulate_usage(
        acc, {"input_tokens": 5, "output_tokens": 2, "reasoning_tokens": 100}
    )
    assert acc["reasoning_tokens"] == 100

    # Fourth turn also reports it — becomes a sum
    accumulate_usage(
        acc, {"input_tokens": 5, "output_tokens": 2, "reasoning_tokens": 50}
    )
    assert acc["reasoning_tokens"] == 150


def test_accumulate_usage_none_input_is_noop() -> None:
    """None / empty turn_usage is a no-op."""
    from agent_seat.native_loop_tools import accumulate_usage

    acc: dict = {"input_tokens": 10, "output_tokens": 5}
    accumulate_usage(acc, None)
    accumulate_usage(acc, {})
    assert acc["input_tokens"] == 10
    assert acc["output_tokens"] == 5


@pytest.mark.asyncio
async def test_native_loop_usage_accumulated_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NativeLoopResult.usage reflects the SUM of all provider turns, not just the last."""

    async def fake_execute(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"ok": True})

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", fake_execute)

    def _anthropic_tool_use_with_usage(
        call_id: str, in_tok: int, out_tok: int
    ) -> dict[str, Any]:
        return {
            "id": "msg_x",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [
                {"type": "tool_use", "id": call_id, "name": "cortex", "input": {"x": 1}}
            ],
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
            "stop_reason": "tool_use",
        }

    # 3 tool turns (each with distinct usage), then terminal
    send = _FakeSend(
        [
            _anthropic_tool_use_with_usage("t1", 10, 5),
            _anthropic_tool_use_with_usage("t2", 20, 8),
            _anthropic_tool_use_with_usage("t3", 15, 3),
            {
                "id": "msg_final",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 30, "output_tokens": 12},
                "stop_reason": "end_turn",
            },
        ]
    )
    req = FrontierRequest(
        messages=[{"role": "user", "content": "do stuff"}],
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
        max_turns=10,
    )
    assert result.content == "done"
    assert result.usage["input_tokens"] == 10 + 20 + 15 + 30
    assert result.usage["output_tokens"] == 5 + 8 + 3 + 12
    # Anthropic never reports reasoning_tokens — should not be present or None
    assert result.usage.get("reasoning_tokens") is None


def test_all_frontier_adapters_implement_synthesis_hooks() -> None:
    """Lock the FrontierAdapter Protocol contract: every cloud adapter the
    native loop dispatches to has ``strip_tools`` and ``append_exhaustion_advisory``
    methods, so the synthesis round fires across providers uniformly. The
    behavioral verification of the synth round itself runs through the
    Anthropic adapter via the test_synthesis_round_* tests above; this is a
    cheap structural guard that catches a future adapter being added without
    the synthesis hooks.
    """
    from llm_adapters.anthropic import AnthropicAdapter
    from llm_adapters.google import GoogleAdapter
    from llm_adapters.responses import ResponsesAPIAdapter

    adapters = [
        AnthropicAdapter(api_key="x"),
        ResponsesAPIAdapter(
            api_key="x", base_url="https://example.test", vendor="openai"
        ),
        ResponsesAPIAdapter(api_key="x", base_url="https://example.test", vendor="xai"),
        GoogleAdapter(api_key="x"),
    ]
    for a in adapters:
        for method in (
            "build_frontier_request",
            "parse_frontier_response",
            "append_tool_round",
            "strip_tools",
            "append_exhaustion_advisory",
        ):
            assert hasattr(a, method), f"{type(a).__name__} missing {method}"
            assert callable(getattr(a, method))


def test_anthropic_strip_tools_and_advisory_shape() -> None:
    """Tight adapter-level shape check for the Anthropic synth hooks."""
    from llm_adapters.anthropic import AnthropicAdapter

    adapter = AnthropicAdapter(api_key="x")
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "x", "description": "x", "input_schema": {}}],
        "tool_choice": "auto",
    }
    adapter.strip_tools(body)
    assert "tools" not in body
    assert "tool_choice" not in body
    adapter.append_exhaustion_advisory(body, "synthesize now")
    assert body["messages"][-1] == {"role": "user", "content": "synthesize now"}


def test_responses_strip_tools_and_advisory_shape() -> None:
    """Tight adapter-level shape check for the Responses (OpenAI/xAI) synth hooks."""
    from llm_adapters.responses import ResponsesAPIAdapter

    adapter = ResponsesAPIAdapter(
        api_key="x", base_url="https://example.test", vendor="openai"
    )
    body = {
        "model": "gpt-5.5",
        "input": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "name": "x", "parameters": {}}],
        "tool_choice": "auto",
    }
    adapter.strip_tools(body)
    assert "tools" not in body
    assert "tool_choice" not in body
    adapter.append_exhaustion_advisory(body, "synthesize now")
    assert body["input"][-1] == {"role": "system", "content": "synthesize now"}


def _google_malformed_fc() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "finishReason": "MALFORMED_FUNCTION_CALL",
                "content": {"role": "model", "parts": []},
            }
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 0},
    }


def _google_terminal(text: str = "done") -> dict[str, Any]:
    return {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"role": "model", "parts": [{"text": text}]},
            }
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 5},
    }


@pytest.mark.asyncio
async def test_google_malformed_retry_preserves_gemini3_temperature() -> None:
    send = _FakeSend([_google_malformed_fc(), _google_terminal("ok")])
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="gemini-3.5-flash",
        max_tokens=100,
        temperature=1.0,
        tools=[
            {
                "type": "function",
                "function": {"name": "cortex", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )
    result = await run_native_tool_loop(
        model="google/gemini-3.5-flash",
        req=req,
        send_native=send,
        max_turns=2,
    )
    assert result.content == "ok"
    retry_body = send.calls[1][1]
    gen_cfg = retry_body.get("generationConfig") or {}
    assert gen_cfg.get("temperature", 1.0) == 1.0


@pytest.mark.asyncio
async def test_google_malformed_retry_coerces_gemini25_temperature() -> None:
    send = _FakeSend([_google_malformed_fc(), _google_terminal("ok")])
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="gemini-2.5-flash",
        max_tokens=100,
        temperature=1.0,
        tools=[
            {
                "type": "function",
                "function": {"name": "cortex", "parameters": {"type": "object"}},
            }
        ],
        mcp_tool_loop=True,
    )
    result = await run_native_tool_loop(
        model="google/gemini-2.5-flash",
        req=req,
        send_native=send,
        max_turns=2,
    )
    assert result.content == "ok"
    retry_body = send.calls[1][1]
    gen_cfg = retry_body.get("generationConfig") or {}
    assert gen_cfg.get("temperature") == 0.7


def test_google_strip_tools_and_advisory_shape() -> None:
    """Tight adapter-level shape check for the Google synth hooks."""
    from llm_adapters.google import GoogleAdapter

    adapter = GoogleAdapter(api_key="x")
    body = {
        "model": "gemini-3-pro",
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "tools": [{"functionDeclarations": [{"name": "x"}]}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
    }
    adapter.strip_tools(body)
    assert "tools" not in body
    assert "toolConfig" not in body
    adapter.append_exhaustion_advisory(body, "synthesize now")
    assert body["contents"][-1] == {
        "role": "user",
        "parts": [{"text": "synthesize now"}],
    }
