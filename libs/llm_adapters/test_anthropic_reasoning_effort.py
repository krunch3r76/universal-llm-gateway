"""Anthropic frontier request tests for adaptive thinking effort wiring."""

from __future__ import annotations

from llm_adapters.anthropic import AnthropicAdapter
from llm_adapters.core import FrontierRequest


def test_anthropic_adaptive_thinking_sends_output_config_effort() -> None:
    adapter = AnthropicAdapter(api_key="test")
    req = FrontierRequest(
        messages=[{"role": "user", "content": "think"}],
        model="claude-opus-4-7",
        thinking={"type": "adaptive"},
        effort="medium",
    )

    _, _, body = adapter.build_frontier_request(req)

    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    assert "budget_tokens" not in body["thinking"]
