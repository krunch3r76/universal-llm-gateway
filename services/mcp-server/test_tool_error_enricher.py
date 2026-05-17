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
        res = await client.call_tool("fs", {"op": "search"})
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
            "sample", {"a": "not-a-number", "b": "also-not-a-number"}
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
        res = await client.call_tool("sample", {"op": "go"})
        env = res.structured_content if hasattr(res, "structured_content") else None
        # Whatever the FastMCP default error path produces, it must NOT be
        # the enricher's structured envelope — enricher must have re-raised.
        if isinstance(env, dict):
            assert env.get("error_type") != "ValidationError" or env.get(
                "tool"
            ) != "sample" or "accepted_params" not in str(env.get("errors", []))
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
    async def _boom(_name: str) -> object:  # type: ignore[unused-argument]
        raise RuntimeError("simulated schema-registry failure")

    mcp.get_tool = _boom  # type: ignore[assignment]

    client = Client(mcp)
    async with client:
        res = await client.call_tool("sample", {"bogus_param": "x"})
        env = res.structured_content
        assert env["error_type"] == "ValidationError"
        assert env["tool"] == "sample"
        err0 = env["errors"][0]
        assert err0["type"] == "unexpected_keyword_argument"
        # Schema lookup failed → accepted_params is empty, hint says "none"
        assert err0["accepted_params"] == []
        assert "none" in err0["hint"]


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
        res = await client.call_tool("sample", {"bogus": "x"})
        env = res.structured_content
        # If the enricher is outermost, it caught the ValidationError; the
        # stub inner middleware saw and re-raised on its way out.
        assert env.get("error_type") == "ValidationError"
        assert "inner_caught_and_reraised" in inner_observations
