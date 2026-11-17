"""In-process tests for ToolErrorEnricher using fastmcp.client.Client against tiny FastMCP.

Covers unexpected_keyword_argument (accepted/required/hint), type-mismatch (expected_type),
dispatch-shape hints, and happy-path pass-through.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from fastmcp import Client, FastMCP
from tools._agent_bus_read import register_agent_bus_read_tool
from tools._agent_tools import JsonArgStr
from tool_error_enricher import register_tool_error_enricher

_DISPATCH_PLACEHOLDER = '<op>", arguments=\'{"<param>": "<value>"}\')'


def _dispatch_tool(mcp: FastMCP, name: str, *, selector: str) -> None:
    """Register a dispatch-shaped stub with real outer signatures."""

    if name == "agent_bus_read":

        @mcp.tool(name=name)
        def agent_bus_read(
            tool: str,
            arguments: JsonArgStr = "{}",
            max_response_bytes: int | None = None,
        ) -> dict[str, str]:
            _ = (tool, arguments, max_response_bytes)
            return {"ok": name}

        return

    if selector == "tool":

        @mcp.tool(name=name)
        def _fn_tool(tool: str, arguments: JsonArgStr = "{}") -> dict[str, str]:
            _ = (tool, arguments)
            return {"ok": name}

    else:

        @mcp.tool(name=name)
        def _fn_op(op: str, arguments: JsonArgStr = "{}") -> dict[str, str]:
            _ = (op, arguments)
            return {"ok": name}


def _example_call_args(example: str) -> dict[str, Any]:
    match = re.match(
        r'^[^(]+\((\w+)="([^"]*)",\s*arguments=\'(.+)\'\)$',
        example,
    )
    assert match, example
    selector, op_val, inner_json = match.groups()
    return {selector: op_val, "arguments": inner_json}


@pytest.mark.asyncio
async def test_unexpected_keyword_argument_enrichment():
    mcp = FastMCP("test-enricher")

    @mcp.tool()
    def fs(op: str, path: str = "") -> dict:
        return {"op": op, "path": path}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool(
            "fs", {"op": "read", "view_range": [1, 60]}, raise_on_error=False
        )
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
        res = await client.call_tool(
            "sample", {"a": "not-a-number", "b": "ok"}, raise_on_error=False
        )
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


@pytest.mark.asyncio
async def test_missing_argument_enrichment_does_not_describe_payload_as_input():
    """Regression: pydantic puts the whole call payload in err['input'] for
    `missing` errors. The enricher must not echo that payload's type as the
    received type — the agent never sent a value for the missing param."""
    mcp = FastMCP("test-enricher")

    @mcp.tool()
    def fs(op: str, sandbox: str) -> dict:
        return {"op": op, "sandbox": sandbox}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool("fs", {"op": "search"}, raise_on_error=False)
        env = res.structured_content
        assert env["error_type"] == "ValidationError"
        assert env["tool"] == "fs"
        err0 = env["errors"][0]
        assert err0["type"] in {"missing", "missing_argument"}
        assert err0["param"] == "sandbox"
        # The enricher MUST override pydantic's "input is the whole payload"
        # default for missing errors; otherwise the hint would say "received
        # dict" for a param the agent didn't send.
        assert err0["input_type"] == "<missing>"
        assert err0["input"] == ""
        assert "is required" in err0["hint"]
        assert "received" not in err0["hint"]
        # If the tool's JSON schema exposes a type for the missing param,
        # the hint should surface it.
        assert err0.get("expected_type") == "string"


@pytest.mark.asyncio
async def test_multiple_errors_in_single_call_all_enriched():
    """A single call can trip multiple validation errors. Every error must
    appear in the envelope's `errors` list with its own per-error hint."""
    mcp = FastMCP("test-enricher")

    @mcp.tool()
    def sample(a: int, b: int) -> dict:
        return {"a": a, "b": b}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool(
            "sample",
            {"a": "not-a-number", "b": "also-not-a-number"},
            raise_on_error=False,
        )
        env = res.structured_content
        assert env["error_type"] == "ValidationError"
        assert len(env["errors"]) == 2
        params = {e["param"] for e in env["errors"]}
        assert params == {"a", "b"}
        for entry in env["errors"]:
            assert "hint" in entry
            assert entry["input_type"] == "str"
            assert entry.get("expected_type") == "integer"


@pytest.mark.asyncio
async def test_body_internal_validation_error_not_enriched():
    """Tool-body-internal ValidationError (raised from inside the tool's impl
    via Model.model_validate, NOT from FastMCP arg-validation) must NOT be
    enriched as if it were an arg-boundary error — doing so would emit the
    tool's parameter list as accepted_params for an unrelated internal Model.

    The middleware re-raises body-internal ValidationErrors; the FastMCP
    error path then surfaces them as a normal tool error. This test asserts
    the response shape is NOT the enricher's structured envelope."""
    from pydantic import BaseModel

    class InternalModel(BaseModel):
        # A field name that doesn't collide with any tool parameter — this is
        # the common case. (Same-name collision is an accepted false-positive
        # edge case for the heuristic.)
        internal_required_field: str

    mcp = FastMCP("test-enricher")

    @mcp.tool()
    def sample(op: str) -> dict:
        # Internal validation raises ValidationError whose error loc is
        # 'internal_required_field' — NOT a tool parameter.
        InternalModel.model_validate({})  # raises
        return {"op": op}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool("sample", {"op": "go"}, raise_on_error=False)
        env = res.structured_content if hasattr(res, "structured_content") else None
        # Whatever the FastMCP default error path produces, it must NOT be
        # the enricher's structured envelope — enricher must have re-raised.
        if isinstance(env, dict):
            assert (
                env.get("error_type") != "ValidationError"
                or env.get("tool") != "sample"
                or "accepted_params" not in str(env.get("errors", []))
            )
        # Also acceptable: the call surfaces as an error result without
        # structured_content shape we recognize.
        assert getattr(res, "is_error", True) is True


@pytest.mark.asyncio
async def test_schema_lookup_failure_degrades_gracefully():
    """If schema lookup fails, the enricher must still return a structured
    envelope (with accepted_params=[]). The 'lookup failure must never poison
    the error path' comment is a real contract — verify it."""
    mcp = FastMCP("test-enricher")

    @mcp.tool()
    def sample(a: int) -> dict:
        return {"a": a}

    register_tool_error_enricher(mcp)

    # Monkey-patch get_tool on the FastMCP instance so the schema lookup
    # raises mid-call. The enricher's inner try/except must swallow it.
    orig_get_tool = mcp.get_tool
    lookup_calls = 0

    async def _flaky_get_tool(
        name: str,
        version: object = None,
    ) -> object:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return await orig_get_tool(name, version=version)
        raise RuntimeError("simulated schema-registry failure")

    mcp.get_tool = _flaky_get_tool  # type: ignore[assignment]

    client = Client(mcp)
    async with client:
        res = await client.call_tool(
            "sample", {"bogus_param": "x"}, raise_on_error=False
        )
        env = res.structured_content
        assert env["error_type"] == "ValidationError"
        assert env["tool"] == "sample"
        bogus = next(e for e in env["errors"] if e.get("param") == "bogus_param")
        assert bogus["type"] == "unexpected_keyword_argument"
        # Schema lookup failed → accepted_params is empty, hint says "none"
        assert bogus["accepted_params"] == []
        assert "none" in bogus["hint"]


@pytest.mark.asyncio
async def test_enricher_registered_outermost_relative_to_later_middleware():
    """Registration-order regression: ToolErrorEnricher must be registered
    BEFORE any other tool-call middleware so that it is the outermost wrapper
    that catches ValidationError before downstream middleware sees it. If a
    later-registered middleware were outer, the structured envelope wouldn't
    be reachable for arg-validation errors.

    Asserts: a stub middleware registered AFTER the enricher runs INSIDE the
    enricher's try/except (its on_call_tool body executes; the enricher then
    translates the ValidationError into the structured envelope)."""
    from fastmcp.server.middleware.middleware import (
        CallNext,
        Middleware,
        MiddlewareContext,
    )

    inner_observations: list[str] = []

    class StubInnerMiddleware(Middleware):
        async def on_call_tool(
            self,
            context: MiddlewareContext,  # type: ignore[type-arg]
            call_next: CallNext,  # type: ignore[type-arg]
        ) -> object:
            inner_observations.append("inner_entered")
            try:
                result = await call_next(context)
            except Exception:
                inner_observations.append("inner_caught_and_reraised")
                raise
            inner_observations.append("inner_returned_normally")
            return result

    mcp = FastMCP("test-order")

    @mcp.tool()
    def sample(a: int) -> dict:
        return {"a": a}

    register_tool_error_enricher(mcp)  # outermost — must register FIRST
    mcp.add_middleware(StubInnerMiddleware())  # inner — registered AFTER

    client = Client(mcp)
    async with client:
        res = await client.call_tool("sample", {"bogus": "x"}, raise_on_error=False)
        env = res.structured_content
        # If the enricher is outermost, it caught the ValidationError; the
        # stub inner middleware saw and re-raised on its way out.
        assert env.get("error_type") == "ValidationError"
        assert "inner_caught_and_reraised" in inner_observations


@pytest.mark.asyncio
async def test_agent_bus_read_specimen_a44137_dispatch_shape_hint():
    mcp = FastMCP("dispatch-hint")
    _dispatch_tool(mcp, "agent_bus_read", selector="tool")
    register_tool_error_enricher(mcp)
    client = Client(mcp)
    payload = {
        "op": "fetch",
        "thread": 24125,
        "projection": "header",
        "last": 3,
    }
    expected_example = (
        'agent_bus_read(tool="fetch", arguments=\'{"thread": 24125, '
        '"projection": "header", "last": 3}\')'
    )
    async with client:
        res = await client.call_tool("agent_bus_read", payload, raise_on_error=False)
    env = res.structured_content
    assert env["example"] == expected_example
    assert "dispatch-shape" in env["hint"]
    assert list(env.keys())[:2] == ["hint", "example"]
    replay_args = _example_call_args(expected_example)
    async with client:
        replay = await client.call_tool("agent_bus_read", replay_args)
    assert replay.structured_content == {"ok": "agent_bus_read"}


@pytest.mark.asyncio
async def test_rag_dispatch_shape_uses_op_selector():
    mcp = FastMCP("dispatch-hint-rag")
    _dispatch_tool(mcp, "rag", selector="op")
    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool(
            "rag", {"tool": "search", "query": "x"}, raise_on_error=False
        )
    env = res.structured_content
    assert env["example"] == 'rag(op="search", arguments=\'{"query": "x"}\')'


@pytest.mark.parametrize(
    "tool_name,selector,bad_payload",
    [
        ("cortex", "tool", {"entity_id": "x", "intent": "card"}),
        ("agent_bus", "tool", {"thread": "1", "last": 3}),
        ("agent_bus_read", "tool", {"op": "fetch", "thread": 1}),
        ("dispatch", "tool", {"pipeline_id": "p"}),
        ("rag", "op", {"tool": "search", "query": "z"}),
    ],
)
@pytest.mark.asyncio
async def test_dispatch_shape_hint_parametrized(tool_name, selector, bad_payload):
    mcp = FastMCP(f"dispatch-{tool_name}")
    _dispatch_tool(mcp, tool_name, selector=selector)
    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool(tool_name, bad_payload, raise_on_error=False)
    env = res.structured_content
    example = env.get("example", "")
    if tool_name == "rag":
        assert example == 'rag(op="search", arguments=\'{"query": "z"}\')'
    elif tool_name == "agent_bus_read":
        assert example == 'agent_bus_read(tool="fetch", arguments=\'{"thread": 1}\')'
    else:
        assert example.endswith(_DISPATCH_PLACEHOLDER)
    assert list(env.keys())[:2] == ["hint", "example"]


@pytest.mark.asyncio
async def test_non_dispatch_tool_has_no_top_level_dispatch_example():
    mcp = FastMCP("test-enricher")
    @mcp.tool()
    def fs(op: str, path: str = "") -> dict:
        return {"op": op, "path": path}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool(
            "fs", {"op": "read", "view_range": [1, 60]}, raise_on_error=False
        )
    env = res.structured_content
    assert "example" not in env


@pytest.mark.asyncio
async def test_type_mismatch_has_no_dispatch_shape_hint():
    mcp = FastMCP("test-enricher")
    @mcp.tool()
    def agent_bus_read(tool: str, arguments: str = "{}") -> dict:
        return {"tool": tool}

    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool(
            "agent_bus_read",
            {"tool": 123, "arguments": "{}"},
            raise_on_error=False,
        )
    env = res.structured_content
    assert "example" not in env


@pytest.mark.asyncio
async def test_dispatch_shape_placeholder_when_arguments_key_present():
    mcp = FastMCP("dispatch-placeholder")
    _dispatch_tool(mcp, "agent_bus_read", selector="tool")
    register_tool_error_enricher(mcp)
    client = Client(mcp)
    async with client:
        res = await client.call_tool(
            "agent_bus_read",
            {"arguments": "{}", "thread": "1"},
            raise_on_error=False,
        )
    env = res.structured_content
    assert (
        env["example"]
        == 'agent_bus_read(tool="<op>", arguments=\'{"<param>": "<value>"}\')'
    )


@pytest.mark.asyncio
async def test_dispatch_hint_builder_failure_returns_envelope_without_hint(monkeypatch):
    mcp = FastMCP("hint-fail")
    _dispatch_tool(mcp, "cortex", selector="tool")
    register_tool_error_enricher(mcp)

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("hint build failed")

    import tool_error_enricher as tee

    monkeypatch.setattr(tee, "_build_dispatch_shape_hint", _boom)
    client = Client(mcp)
    async with client:
        res = await client.call_tool(
            "cortex", {"entity_id": "x"}, raise_on_error=False
        )
    env = res.structured_content
    assert env["error_type"] == "ValidationError"
    assert "example" not in env
    assert "hint" not in env or "dispatch-style" not in str(env.get("hint", "")).lower()


@pytest.mark.asyncio
async def test_validation_error_event_records_dispatch_shape_hint(monkeypatch):
    mcp = FastMCP("event-dispatch")
    _dispatch_tool(mcp, "agent_bus_read", selector="tool")
    register_tool_error_enricher(mcp)
    captured: list[dict[str, Any]] = []

    import tool_error_enricher as tee

    def _record(signal: str, **fields: object) -> None:
        if signal == "mcp.tool.validation.error":
            captured.append(dict(fields))

    monkeypatch.setattr(tee, "record", _record)
    client = Client(mcp)
    async with client:
        await client.call_tool(
            "agent_bus_read", {"op": "fetch", "thread": 1}, raise_on_error=False
        )
    assert captured
    assert captured[-1].get("dispatch_shape_hint") is True


@pytest.mark.asyncio
async def test_agent_bus_read_flat_dispatch_hint_replay_string_last_reaches_fetch():
    relay_calls: list[int] = []

    def relay(service: str, method: str, path: str, **kwargs: object) -> dict[str, list]:
        del service, method, path, kwargs
        relay_calls.append(1)
        return {"turns": []}

    mcp = FastMCP("dispatch-replay-fetch-last")
    register_agent_bus_read_tool(mcp)
    register_tool_error_enricher(mcp)
    client = Client(mcp)
    payload = {
        "op": "fetch",
        "thread": "24132",
        "projection": "header",
        "last": "1",
    }
    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        async with client:
            res = await client.call_tool("agent_bus_read", payload, raise_on_error=False)
        env = res.structured_content
        assert "example" in env
        replay_args = _example_call_args(env["example"])
        async with client:
            replay = await client.call_tool("agent_bus_read", replay_args)
    assert relay_calls, "relay should run on successful replay"
    assert "error" not in str(replay.structured_content)


@pytest.mark.asyncio
async def test_agent_bus_read_dispatch_hint_example_numeric_inner_types():
    mcp = FastMCP("dispatch-example-numeric-last")
    register_agent_bus_read_tool(mcp)
    register_tool_error_enricher(mcp)
    client = Client(mcp)
    payload = {
        "op": "fetch",
        "thread": "24132",
        "projection": "header",
        "last": "1",
    }
    async with client:
        res = await client.call_tool("agent_bus_read", payload, raise_on_error=False)
    env = res.structured_content
    inner = json.loads(_example_call_args(env["example"])["arguments"])
    assert inner["last"] == 1
    assert isinstance(inner["last"], int)


@pytest.mark.asyncio
async def test_agent_bus_read_get_dispatch_hint_replay_string_turn_numbers_reaches_get():
    relay_calls: list[int] = []

    def relay(service: str, method: str, path: str, **kwargs: object) -> dict[str, Any]:
        del service, method, path, kwargs
        relay_calls.append(1)
        return {
            "id": 1,
            "thread": "t",
            "turn_number": 1,
            "body": "x",
            "to_agent": "all",
        }

    mcp = FastMCP("dispatch-replay-get-turn-numbers")
    register_agent_bus_read_tool(mcp)
    register_tool_error_enricher(mcp)
    client = Client(mcp)
    payload = {"op": "get", "thread": "t", "turn_numbers": ["1"]}
    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        async with client:
            res = await client.call_tool("agent_bus_read", payload, raise_on_error=False)
        env = res.structured_content
        assert "example" in env
        replay_args = _example_call_args(env["example"])
        async with client:
            replay = await client.call_tool("agent_bus_read", replay_args)
    assert relay_calls, "relay should run on successful get replay"
    body = replay.structured_content
    assert not (
        isinstance(body, dict)
        and body.get("reason") == "get_turn_numbers_invalid"
    )
