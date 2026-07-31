"""Parity test for the ``CapabilityDispatch`` wire serializer (G11 projection).

Pins ``to_wire_dict(resolve(model))`` to the exact shape that rides ``/v1/models``
across all three live registry surfaces (anthropic, openai_responses, google).

NOTE on the oracle: the wire shape is contractually
``CapabilityDispatchFacet.from_dispatch(d).model_dump(mode="json", exclude_none=True)``
(gateway schema). That gateway module lives under
``services/_universal-llm-gateway/src/`` — a hyphenated, leading-underscore path
that is not importable from the libs test environment, and importing a service
schema from libs would violate [universal:libs-first]. So this test pins the shape
with hand-derived golden dicts (the contract, frozen); the from_dispatch-oracle
drift-guard (``to_wire_dict(d) == from_dispatch(d).model_dump(...)``) lives in the
gateway service suite, where both libs and the facet are importable.
"""

from __future__ import annotations

import pytest

from llm_adapters.capability_dispatch import (
    CatalogMissError,
    resolve,
    to_wire_dict,
)

# sorted(VALID_REASONING_EFFORTS) — the effort vocabulary, as the wire emits it.
_ALL_EFFORTS = ["high", "low", "max", "medium", "minimal", "none", "xhigh"]

# Golden wire dicts, derived from the registry resolution rules.
_GOLDEN: dict[str, dict] = {
    # Anthropic adaptive family: ceiling present, no floor; reasoning adaptive.
    "anthropic/claude-opus-4-8": {
        "api_surface": "anthropic",
        "max_output": {
            "default": 128000,
            "native_field": "max_tokens",
            "over_ceiling": "clamp",
            "ceiling": 128000,
        },
        "params": {},
        "reasoning": {
            "native_field_path": "thinking",
            "value_kind": "adaptive",
            "accepted_values": _ALL_EFFORTS,
        },
    },
    # Anthropic budget-mode family: token_budget reasoning carries budget_map.
    "anthropic/claude-sonnet-4-5": {
        "api_surface": "anthropic",
        "max_output": {
            "default": 64000,
            "native_field": "max_tokens",
            "over_ceiling": "clamp",
            "ceiling": 64000,
        },
        "params": {},
        "reasoning": {
            "native_field_path": "thinking",
            "value_kind": "token_budget",
            "accepted_values": ["high", "low", "medium"],
            "budget_map": {"low": 2048, "medium": 8192, "high": 24000},
        },
    },
    # OpenAI Responses surface: floor present, ceiling absent; no implicit default.
    "openai/gpt-5.5": {
        "api_surface": "openai_responses",
        "max_output": {
            "default": 131072,
            "native_field": "max_output_tokens",
            "over_ceiling": "clamp",
            "floor": 16384,
        },
        "params": {},
        "reasoning": {
            "native_field_path": "reasoning.effort",
            "value_kind": "effort_string",
            "accepted_values": _ALL_EFFORTS,
        },
    },
    # xAI shares the Responses surface and carries reasoning.default = "high".
    "xai/grok-4.5": {
        "api_surface": "openai_responses",
        "max_output": {
            "default": 131072,
            "native_field": "max_output_tokens",
            "over_ceiling": "clamp",
            "floor": 16384,
        },
        "params": {},
        "reasoning": {
            "native_field_path": "reasoning.effort",
            "value_kind": "effort_string",
            "accepted_values": _ALL_EFFORTS,
            "default": "high",
        },
    },
    # Google: neither ceiling nor floor; thinkingConfig effort path.
    "google/gemini-3-pro": {
        "api_surface": "google_generate_content",
        "max_output": {
            "default": 131072,
            "native_field": "maxOutputTokens",
            "over_ceiling": "clamp",
        },
        "params": {},
        "reasoning": {
            "native_field_path": "thinkingConfig",
            "value_kind": "effort_string",
            "accepted_values": _ALL_EFFORTS,
        },
    },
}


@pytest.mark.parametrize("model", sorted(_GOLDEN))
def test_to_wire_dict_matches_golden(model: str) -> None:
    assert to_wire_dict(resolve(model)) == _GOLDEN[model]


def test_responses_has_floor_no_ceiling() -> None:
    mo = to_wire_dict(resolve("openai/gpt-5.5"))["max_output"]
    assert "floor" in mo and "ceiling" not in mo


def test_anthropic_has_ceiling_no_floor() -> None:
    mo = to_wire_dict(resolve("anthropic/claude-opus-4-8"))["max_output"]
    assert "ceiling" in mo and "floor" not in mo


def test_google_has_neither_ceiling_nor_floor() -> None:
    mo = to_wire_dict(resolve("google/gemini-3-pro"))["max_output"]
    assert "ceiling" not in mo and "floor" not in mo


def test_over_ceiling_always_present_and_params_empty() -> None:
    for model in _GOLDEN:
        wire = to_wire_dict(resolve(model))
        assert wire["max_output"]["over_ceiling"] == "clamp"
        assert wire["params"] == {}


def test_uninferable_provider_raises_catalog_miss() -> None:
    with pytest.raises(CatalogMissError):
        resolve("frobnozz/some-model")
