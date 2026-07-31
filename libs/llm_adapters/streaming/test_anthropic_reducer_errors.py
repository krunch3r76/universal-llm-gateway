"""Error handling and fixture integration tests for AnthropicReducer.

Separated from ``test_anthropic_reducer.py`` to keep both files under the
SLOC ceiling.  Covers:

- SSEProviderError on stream-level error event
- Integration: drive accumulate_sse_stream over real captured fixtures
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sse.core import SSEMessage
from sse.protocols import SSEProviderError

from llm_adapters.streaming.anthropic import AnthropicReducer, _AnthropicState

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Unit: terminal_error raises SSEProviderError
# ---------------------------------------------------------------------------


def test_terminal_error_raises_provider_error() -> None:
    r = AnthropicReducer()
    state = r.initial_state()
    err_evt = SSEMessage(data='{"error": {"type": "overloaded"}}', event="error")
    with pytest.raises(SSEProviderError, match="Anthropic stream error"):
        r.terminal_error(state, err_evt)


# ---------------------------------------------------------------------------
# Integration: accumulate_sse_stream over real fixture
# ---------------------------------------------------------------------------


async def _run_fixture(fixture_path: Path) -> _AnthropicState:
    from sse.accumulator import accumulate_sse_stream

    raw = fixture_path.read_bytes()

    async def _byte_iter():  # type: ignore[return]
        yield raw

    reducer = AnthropicReducer()
    return await accumulate_sse_stream(_byte_iter(), reducer, stall_timeout=10.0)


@pytest.mark.skipif(
    not (FIXTURES / "anthropic_tooluse.txt").exists(),
    reason="Anthropic tool-use fixture not captured",
)
def test_anthropic_fixture_tooluse_produces_tool_call() -> None:
    """AnthropicReducer over real Anthropic SSE must yield ≥1 tool_use block."""
    from llm_adapters.anthropic import AnthropicAdapter

    state = asyncio.run(_run_fixture(FIXTURES / "anthropic_tooluse.txt"))
    terminal = AnthropicReducer.to_terminal_dict(state)

    tool_blocks = [
        b for b in terminal.get("content", []) if b.get("type") == "tool_use"
    ]
    assert tool_blocks, "expected ≥1 tool_use block in terminal content"
    tb = tool_blocks[0]
    assert tb["name"] == "get_weather"
    assert isinstance(tb["input"], dict)
    assert tb["input"].get("location"), "tool input must have a location key"

    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    parsed = adapter.parse_frontier_response(terminal)
    tool_calls = parsed.get("tool_calls") or []
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "get_weather"
    assert tool_calls[0]["input"]["location"]
