"""Google adapter tests for thinkingLevel / thinkingBudget wiring.

Locks the lowercase-enum contract (per
docs/thirdparty/google-api/upstream/thinking.md) for Gemini 3 and the
budget translation for Gemini 2.5.
"""

from __future__ import annotations

import pytest

from llm_adapters.core import FrontierRequest
from llm_adapters.google import GoogleAdapter


def _build(model: str, effort: str | None) -> dict:
    adapter = GoogleAdapter(api_key="test")
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model=model,
        thinking={"effort": effort} if effort else None,
    )
    _, _, body = adapter.build_frontier_request(req)
    return body


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high"])
def test_gemini3_thinking_level_is_lowercase(level: str) -> None:
    body = _build("gemini-3-flash-preview", level)
    assert body["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": level,
        "includeThoughts": True,
    }


def test_gemini3_uppercase_input_is_normalized_lowercase() -> None:
    """Defensive: even if a caller hands the adapter an uppercase value
    (e.g. from a misconfigured handler), the wire shape stays lowercase."""
    body = _build("gemini-3.1-pro-preview", "HIGH")
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "high"


@pytest.mark.parametrize(
    "level,budget",
    [("low", 1024), ("medium", 8192), ("high", 24576)],
)
def test_gemini25_uses_thinking_budget(level: str, budget: int) -> None:
    body = _build("gemini-2.5-pro", level)
    assert body["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": budget,
        "includeThoughts": True,
    }


@pytest.mark.parametrize("level", ["minimal", "none", "xhigh", "max"])
def test_gemini25_unmapped_effort_skips_thinking_config(level: str) -> None:
    """Gemini 2.5 has no documented mapping for extended-vocabulary effort
    values; the adapter falls through to the model default rather than
    inventing a budget."""
    body = _build("gemini-2.5-flash", level)
    assert "thinkingConfig" not in body.get("generationConfig", {})


def test_no_thinking_no_thinking_config() -> None:
    body = _build("gemini-3-flash-preview", None)
    assert "thinkingConfig" not in body.get("generationConfig", {})
