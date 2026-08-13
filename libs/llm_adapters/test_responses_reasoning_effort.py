"""Unit tests for reasoning.effort gating — registry predicates + G9 boundary reject.

Post-Fence-D contract:
- The reasoning-effort support predicates live in the ``capability_dispatch``
  registry (``openai_supports_reasoning_effort`` / ``xai_supports_reasoning_effort``).
- The ResponsesAPIAdapter injects ``reasoning.effort`` UNCONDITIONALLY — the
  adapter no longer silently drops unsupported effort. Unsupported effort is
  rejected loudly at the ``resolve_dispatch`` boundary (G9 ``ProtocolError``).
- Reasoning OpenAI models (gpt-5.x, o-series) + xAI grok-3/grok-4.6 still inject
  effort; the boundary rejects gpt-4o / pre-4.3 grok with a declared effort.
"""

from __future__ import annotations

import pytest

from llm_adapters import FrontierRequest
from llm_adapters.capability_dispatch import (
    ProtocolError,
    openai_supports_reasoning_effort,
    resolve_dispatch,
    xai_supports_reasoning_effort,
)
from llm_adapters.responses import ResponsesAPIAdapter


def _adapter(vendor: str) -> ResponsesAPIAdapter:
    base = "https://api.x.ai/v1" if vendor == "xai" else "https://api.openai.com/v1"
    return ResponsesAPIAdapter(api_key="k-test", base_url=base, vendor=vendor)


def _req_with_effort(model: str, effort: str = "high") -> FrontierRequest:
    # max_tokens is a resolved int post-boundary (the adapter is a pure consumer).
    return FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model=model,
        max_tokens=50000,
        thinking={"effort": effort},
    )


# ---------------------------------------------------------------------------
# Registry support predicates
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
    assert openai_supports_reasoning_effort(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4-turbo",
        "gpt-4",
    ],
)
def test_openai_supports_reasoning_effort_false(model: str) -> None:
    assert openai_supports_reasoning_effort(model) is False


# ---------------------------------------------------------------------------
# Adapter: reasoning models → reasoning.effort injected (unconditional)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["gpt-5.4", "gpt-5.3-codex", "o3-mini", "o1-mini"])
def test_openai_reasoning_model_sends_effort(model: str) -> None:
    req = _req_with_effort(model, effort="medium")
    _url, _headers, body = _adapter("openai").build_frontier_request(req)
    assert body.get("reasoning") == {"effort": "medium"}, (
        f"Expected reasoning.effort for model={model}"
    )


@pytest.mark.parametrize(
    "model",
    [
        "grok-3-mini",
        "grok-3",
        "grok-3-mini-fast",
    ],
)
def test_xai_grok3_sends_effort(model: str) -> None:
    assert xai_supports_reasoning_effort(model) is True
    req = _req_with_effort(model, effort="low")
    _url, _headers, body = _adapter("xai").build_frontier_request(req)
    assert body.get("reasoning") == {"effort": "low"}, (
        f"Expected reasoning.effort for xAI model={model}"
    )


@pytest.mark.parametrize("model", ["grok-4.6", "grok-4.3"])
def test_xai_grok45_sends_effort(model: str) -> None:
    assert xai_supports_reasoning_effort(model) is True
    req = _req_with_effort(model, effort="medium")
    _url, _headers, body = _adapter("xai").build_frontier_request(req)
    assert body.get("reasoning") == {"effort": "medium"}, (
        f"Expected reasoning.effort for xAI model={model}"
    )


# ---------------------------------------------------------------------------
# G9 boundary reject: unsupported declared effort raises ProtocolError
# (replaces the adapter's prior silent drop for gpt-4o / pre-4.3 grok).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model", ["openai/gpt-4o", "openai/gpt-4.1", "openai/gpt-4o-mini"]
)
def test_openai_non_reasoning_model_rejected_at_boundary(model: str) -> None:
    with pytest.raises(ProtocolError) as exc_info:
        resolve_dispatch(model, reasoning_effort="high")
    assert any(v.knob == "reasoning.effort" for v in exc_info.value.violations)


@pytest.mark.parametrize(
    "model",
    [
        "xai/grok-4.20-0309-non-reasoning",
        "xai/grok-4-fast-reasoning",
        "xai/grok-4",
    ],
)
def test_xai_pre_45_grok_rejected_at_boundary(model: str) -> None:
    assert xai_supports_reasoning_effort(model.split("/", 1)[-1]) is False
    with pytest.raises(ProtocolError) as exc_info:
        resolve_dispatch(model, reasoning_effort="high")
    assert any(v.knob == "reasoning.effort" for v in exc_info.value.violations)


# ---------------------------------------------------------------------------
# Baseline: no thinking → no reasoning key
# ---------------------------------------------------------------------------


def test_no_thinking_no_reasoning_key() -> None:
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-5.4",
        max_tokens=50000,
    )
    _url, _headers, body = _adapter("openai").build_frontier_request(req)
    assert "reasoning" not in body
