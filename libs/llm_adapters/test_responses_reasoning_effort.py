"""Unit tests for reasoning.effort model-gating in ResponsesAPIAdapter.

Covers _openai_supports_reasoning_effort and the conditional injection
logic in build_frontier_request:

- Reasoning OpenAI models (gpt-5.x, o-series) receive reasoning.effort
- Non-reasoning OpenAI models (gpt-4o, gpt-4.1) get reasoning stripped
- xAI grok-3 family receives reasoning.effort
- xAI grok-4.3 receives reasoning.effort (supported per 2026 xAI docs)
- Earlier grok-4 family (pre-4.3) gets reasoning stripped (built-in, not controllable)
- No thinking → no reasoning key in body (baseline)
"""

from __future__ import annotations

import pytest

from llm_adapters import FrontierRequest
from llm_adapters.responses import (
    ResponsesAPIAdapter,
    _openai_supports_reasoning_effort,
    _xai_supports_reasoning_effort,
)


def _adapter(vendor: str) -> ResponsesAPIAdapter:
    base = "https://api.x.ai/v1" if vendor == "xai" else "https://api.openai.com/v1"
    return ResponsesAPIAdapter(api_key="k-test", base_url=base, vendor=vendor)


def _req_with_effort(model: str, effort: str = "high") -> FrontierRequest:
    return FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model=model,
        thinking={"effort": effort},
    )


# ---------------------------------------------------------------------------
# _openai_supports_reasoning_effort unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.4",
        "gpt-5.3",
        "gpt-5.3-codex",
        "o1",
        "o1-mini",
        "o1-preview",
        "o3",
        "o3-mini",
        "o4-mini",
    ],
)
def test_openai_supports_reasoning_effort_true(model: str) -> None:
    assert _openai_supports_reasoning_effort(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4-turbo",
        "gpt-4",
        # Note: gpt-5-search-api starts with "gpt-5" so _openai_supports_reasoning_effort
        # returns True for it — but it never reaches this adapter in practice because
        # frontier_dispatch admission rejects it via _is_chat_completions_only first.
    ],
)
def test_openai_supports_reasoning_effort_false(model: str) -> None:
    assert _openai_supports_reasoning_effort(model) is False


# ---------------------------------------------------------------------------
# Adapter: OpenAI reasoning models → reasoning.effort injected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["gpt-5.4", "gpt-5.3-codex", "o3-mini", "o1-mini"])
def test_openai_reasoning_model_sends_effort(model: str) -> None:
    req = _req_with_effort(model, effort="medium")
    _url, _headers, body = _adapter("openai").build_frontier_request(req)
    assert body.get("reasoning") == {"effort": "medium"}, (
        f"Expected reasoning.effort for model={model}"
    )


# ---------------------------------------------------------------------------
# Adapter: OpenAI non-reasoning models → reasoning stripped (no 400)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4.1", "gpt-4o-mini"])
def test_openai_non_reasoning_model_strips_effort(model: str) -> None:
    req = _req_with_effort(model, effort="high")
    _url, _headers, body = _adapter("openai").build_frontier_request(req)
    assert "reasoning" not in body, (
        f"reasoning must not appear for non-reasoning model={model}"
    )


# ---------------------------------------------------------------------------
# Adapter: xAI grok-3 → reasoning.effort injected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "grok-3-mini",
        "grok-3",
        "grok-3-mini-fast",
    ],
)
def test_xai_grok3_sends_effort(model: str) -> None:
    assert _xai_supports_reasoning_effort(model) is True
    req = _req_with_effort(model, effort="low")
    _url, _headers, body = _adapter("xai").build_frontier_request(req)
    assert body.get("reasoning") == {"effort": "low"}, (
        f"Expected reasoning.effort for xAI model={model}"
    )


# ---------------------------------------------------------------------------
# Adapter: xAI grok-4.3 → reasoning.effort injected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["grok-4.3", "grok-4.20-multi-agent-0309"])
def test_xai_grok43_sends_effort(model: str) -> None:
    assert _xai_supports_reasoning_effort(model) is True
    req = _req_with_effort(model, effort="medium")
    _url, _headers, body = _adapter("xai").build_frontier_request(req)
    assert body.get("reasoning") == {"effort": "medium"}, (
        f"Expected reasoning.effort for xAI model={model}"
    )


# ---------------------------------------------------------------------------
# Adapter: xAI grok-4 (pre-4.3) → reasoning stripped (built-in, not controllable)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "grok-4.20-0309-reasoning",
        "grok-4.20-0309-non-reasoning",
        "grok-4-fast-reasoning",
        "grok-4",
    ],
)
def test_xai_grok4_strips_effort(model: str) -> None:
    assert _xai_supports_reasoning_effort(model) is False
    req = _req_with_effort(model, effort="high")
    _url, _headers, body = _adapter("xai").build_frontier_request(req)
    assert "reasoning" not in body, (
        f"reasoning must not appear for grok-4 model={model}"
    )


# ---------------------------------------------------------------------------
# Baseline: no thinking → no reasoning key
# ---------------------------------------------------------------------------


def test_no_thinking_no_reasoning_key() -> None:
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-5.4",
    )
    _url, _headers, body = _adapter("openai").build_frontier_request(req)
    assert "reasoning" not in body
