"""Fixture integration tests for OpenAIResponsesReducer.

Separated from ``test_openai_reducer.py`` to keep both files under the SLOC
ceiling.  Drives ``accumulate_sse_stream`` over real captured SSE streams
for OpenAI Responses API and xAI.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_adapters.streaming.openai import OpenAIResponsesReducer, _OpenAIResponsesState

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_fixture(fixture_path: Path) -> _OpenAIResponsesState:
    from sse.accumulator import accumulate_sse_stream

    raw = fixture_path.read_bytes()

    async def _byte_iter():  # type: ignore[return]
        yield raw

    reducer = OpenAIResponsesReducer()
    return await accumulate_sse_stream(_byte_iter(), reducer, stall_timeout=10.0)


# ---------------------------------------------------------------------------
# Integration: simple text response
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (FIXTURES / "openai_simple.txt").exists(),
    reason="OpenAI fixture not captured",
)
def test_openai_fixture_produces_non_empty_content() -> None:
    state = asyncio.run(_run_fixture(FIXTURES / "openai_simple.txt"))
    result = OpenAIResponsesReducer.to_terminal_dict(state)
    assert result, "terminal_response must not be empty"
    assert result.get("output"), "response.output must be non-empty"
    usage = result.get("usage") or {}
    assert usage.get("output_tokens", 0) > 0, "output_tokens must be > 0"

    from llm_adapters.responses import ResponsesAPIAdapter

    adapter = ResponsesAPIAdapter(
        api_key="k", base_url="https://api.openai.com/v1", vendor="openai"
    )
    parsed = adapter.parse_frontier_response(result)
    assert parsed["content"].strip(), "parsed content must be non-empty"
    assert parsed["usage"]["output_tokens"] > 0


@pytest.mark.skipif(
    not (FIXTURES / "xai_simple.txt").exists(),
    reason="xAI fixture not captured",
)
def test_xai_fixture_produces_non_empty_content() -> None:
    state = asyncio.run(_run_fixture(FIXTURES / "xai_simple.txt"))
    result = OpenAIResponsesReducer.to_terminal_dict(state)
    assert result, "terminal_response must not be empty"
    output = result.get("output") or []
    assert output, "response.output must be non-empty"

    item_types = {item.get("type") for item in output if isinstance(item, dict)}
    assert "message" in item_types, "expected a message output item"

    from llm_adapters.responses import ResponsesAPIAdapter

    adapter = ResponsesAPIAdapter(
        api_key="k", base_url="https://api.x.ai/v1", vendor="xai"
    )
    parsed = adapter.parse_frontier_response(result)
    assert parsed["content"].strip(), "parsed content must be non-empty"
    assert parsed["usage"]["output_tokens"] > 0


# ---------------------------------------------------------------------------
# Integration: tool-use fixtures
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (FIXTURES / "openai_tooluse.txt").exists(),
    reason="OpenAI tool-use fixture not captured",
)
def test_openai_fixture_tooluse_produces_tool_call() -> None:
    """OpenAIResponsesReducer over real fixture must yield ≥1 function_call item."""
    from llm_adapters.responses import ResponsesAPIAdapter

    state = asyncio.run(_run_fixture(FIXTURES / "openai_tooluse.txt"))
    result = OpenAIResponsesReducer.to_terminal_dict(state)
    assert result, "terminal_response must not be empty"

    output = result.get("output") or []
    fc_items = [
        i for i in output if isinstance(i, dict) and i.get("type") == "function_call"
    ]
    assert fc_items, "expected ≥1 function_call output item"
    fc = fc_items[0]
    assert fc["name"] == "get_weather"
    assert "Paris" in (fc.get("arguments") or "")

    adapter = ResponsesAPIAdapter(
        api_key="k", base_url="https://api.openai.com/v1", vendor="openai"
    )
    parsed = adapter.parse_frontier_response(result)
    tool_calls = parsed.get("tool_calls") or []
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "get_weather"
    assert "Paris" in (tool_calls[0].get("arguments") or "")


@pytest.mark.skipif(
    not (FIXTURES / "xai_tooluse.txt").exists(),
    reason="xAI tool-use fixture not captured",
)
def test_xai_fixture_tooluse_produces_tool_call() -> None:
    """OpenAIResponsesReducer over real xAI fixture must yield ≥1 function_call item."""
    from llm_adapters.responses import ResponsesAPIAdapter

    state = asyncio.run(_run_fixture(FIXTURES / "xai_tooluse.txt"))
    result = OpenAIResponsesReducer.to_terminal_dict(state)
    assert result, "terminal_response must not be empty"

    output = result.get("output") or []
    fc_items = [
        i for i in output if isinstance(i, dict) and i.get("type") == "function_call"
    ]
    assert fc_items, "expected ≥1 function_call output item"
    assert fc_items[0]["name"] == "get_weather"

    adapter = ResponsesAPIAdapter(
        api_key="k", base_url="https://api.x.ai/v1", vendor="xai"
    )
    parsed = adapter.parse_frontier_response(result)
    tool_calls = parsed.get("tool_calls") or []
    assert len(tool_calls) >= 1
    assert tool_calls[0]["name"] == "get_weather"
