"""Fixture integration tests for GoogleStreamReducer.

Separated from ``test_google_reducer.py`` to keep both files under the SLOC
ceiling.  Drives ``accumulate_sse_stream`` over real captured SSE streams
and verifies ``GoogleAdapter.parse_frontier_response`` consumes the output.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_adapters.streaming.google import GoogleStreamReducer, _GoogleState

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_fixture(fixture_path: Path) -> _GoogleState:
    from sse.accumulator import accumulate_sse_stream

    raw = fixture_path.read_bytes()

    async def _byte_iter():  # type: ignore[return]
        yield raw

    reducer = GoogleStreamReducer()
    return await accumulate_sse_stream(_byte_iter(), reducer, stall_timeout=10.0)


# ---------------------------------------------------------------------------
# Integration: simple text stream
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (FIXTURES / "google_simple.txt").exists(),
    reason="Google simple fixture not captured",
)
def test_google_simple_fixture_merges_text_deltas() -> None:
    """Multi-chunk text stream must produce a single merged text part."""
    from llm_adapters.google import GoogleAdapter

    state = asyncio.run(_run_fixture(FIXTURES / "google_simple.txt"))
    result = GoogleStreamReducer.to_terminal_dict(state)
    assert result["candidates"], "candidates must be non-empty"
    parts = result["candidates"][0]["content"]["parts"]
    text_parts = [p for p in parts if "text" in p and not p.get("thought")]
    assert len(text_parts) == 1
    merged_text = text_parts[0]["text"]
    assert "Sourdough" in merged_text
    assert "digest" in merged_text
    assert result["candidates"][0]["finishReason"] == "STOP"
    assert result["usageMetadata"]["candidatesTokenCount"] > 0

    adapter = GoogleAdapter(api_key="k")
    parsed = adapter.parse_frontier_response(result)
    assert parsed["provider"] == "google"
    assert "Sourdough" in parsed["content"]
    assert parsed["usage"]["output_tokens"] > 0
    assert parsed["finish_reason"] == "STOP"


@pytest.mark.skipif(
    not (FIXTURES / "google_tooluse.txt").exists(),
    reason="Google tool-use fixture not captured",
)
def test_google_tooluse_fixture_yields_function_call() -> None:
    from llm_adapters.google import GoogleAdapter

    state = asyncio.run(_run_fixture(FIXTURES / "google_tooluse.txt"))
    result = GoogleStreamReducer.to_terminal_dict(state)
    parts = result["candidates"][0]["content"]["parts"]
    fc_parts = [p for p in parts if isinstance(p, dict) and "functionCall" in p]
    assert fc_parts, "expected a functionCall part"
    assert fc_parts[0]["functionCall"]["name"] == "get_weather"
    assert fc_parts[0]["functionCall"]["args"].get("location") == "Paris"
    assert fc_parts[0].get("thoughtSignature"), "thoughtSignature must survive replay"

    adapter = GoogleAdapter(api_key="k")
    parsed = adapter.parse_frontier_response(result)
    tool_calls = parsed.get("tool_calls") or []
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "get_weather"
    assert tool_calls[0]["input"] == {"location": "Paris"}


@pytest.mark.skipif(
    not (FIXTURES / "google_thinking.txt").exists(),
    reason="Google thinking fixture not captured",
)
def test_google_thinking_fixture_separates_thought_and_answer() -> None:
    """Thought deltas must stay in thought-flagged parts; the answer is separate."""
    from llm_adapters.google import GoogleAdapter

    state = asyncio.run(_run_fixture(FIXTURES / "google_thinking.txt"))
    result = GoogleStreamReducer.to_terminal_dict(state)
    parts = result["candidates"][0]["content"]["parts"]
    thought_parts = [p for p in parts if p.get("thought")]
    answer_parts = [p for p in parts if "text" in p and not p.get("thought")]
    assert thought_parts, "expected at least one thought-flagged part"
    assert answer_parts, "expected at least one plain-text answer part"
    answer = "".join(p["text"] for p in answer_parts)
    assert "391" in answer

    adapter = GoogleAdapter(api_key="k")
    parsed = adapter.parse_frontier_response(result)
    assert "391" in parsed["content"]
    assert parsed["thinking"] is not None
    assert "thought" not in parsed["content"].lower()
