"""Anthropic frontier request tests for adaptive thinking effort wiring."""

from __future__ import annotations

import pytest

from llm_adapters.anthropic import AnthropicAdapter
from llm_adapters.capability_dispatch import ProtocolError, resolve_dispatch
from llm_adapters.core import FrontierRequest


def test_anthropic_adaptive_thinking_sends_output_config_effort() -> None:
    adapter = AnthropicAdapter(api_key="test")
    req = FrontierRequest(
        messages=[{"role": "user", "content": "think"}],
        model="claude-opus-4-7",
        max_tokens=64000,
        thinking={"type": "adaptive"},
        effort="medium",
    )

    _, _, body = adapter.build_frontier_request(req)

    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    assert "budget_tokens" not in body["thinking"]


@pytest.mark.parametrize("effort", ["minimal", "max", "xhigh", "none"])
def test_token_budget_unmapped_effort_rejected_at_boundary(effort: str) -> None:
    with pytest.raises(ProtocolError) as exc_info:
        resolve_dispatch("anthropic/claude-sonnet-4-5", reasoning_effort=effort)
    violations = exc_info.value.violations
    assert len(violations) == 1
    assert violations[0].knob == "reasoning.effort"
    assert violations[0].reject_code == "unsupported_by_model"
    assert "valid: high, low, medium" in violations[0].message
