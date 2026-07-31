"""Tests for frontier model capability cards."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from model_capabilities import (
    CARD_VERSION,
    MODEL_CAPABILITY_CARDS,
    CapabilityCardError,
    capability_card,
    inline_only,
    mcp_capable,
    mcp_client_tool_loop,
    mcp_remote_connector,
    server_side_tools,
    skills_mount_backend,
)
from model_id import ModelId


def test_card_version_is_date_string() -> None:
    assert CARD_VERSION == "2026-07-21"


def test_capability_card_lookup_normalized() -> None:
    card = capability_card("anthropic/claude-opus-4-8")
    assert card.mcp_client_tool_loop is True
    assert card.mcp_remote_connector is True


def test_shared_card_constants_for_provider_variants() -> None:
    anthropic = capability_card("anthropic/claude-sonnet-4-6")
    assert capability_card("anthropic/claude-opus-4") == anthropic


def test_derived_mcp_capable_and_inline_only() -> None:
    assert mcp_capable("openai/gpt-5.5") is True
    assert inline_only("openai/gpt-5.5") is False
    assert mcp_capable("xai/grok-4.5") is True
    assert inline_only("xai/grok-4.5") is False


def test_missing_card_raises_structured_error() -> None:
    with pytest.raises(CapabilityCardError) as exc:
        capability_card("openai/gpt-4o")
    err = exc.value
    assert err.reason_code == "capability_card_missing"
    assert err.model == "openai/gpt-4o"
    assert err.capability_field == "card"


def test_unset_field_raises_structured_error() -> None:
    from model_capabilities import model_capabilities as mod

    mod.MODEL_CAPABILITY_CARDS["openai/test-unset"] = mod.ModelCapabilityCard(
        mcp_client_tool_loop=None,
        mcp_remote_connector=False,
        server_side_tools=(),
        skills_mount_backend="none",
    )
    try:
        with pytest.raises(CapabilityCardError) as exc:
            mcp_client_tool_loop("openai/test-unset")
        err = exc.value
        assert err.reason_code == "capability_card_field_missing"
        assert err.capability_field == "mcp_client_tool_loop"
    finally:
        mod.MODEL_CAPABILITY_CARDS.pop("openai/test-unset", None)


def test_cursor_model_is_out_of_card_domain() -> None:
    with pytest.raises(CapabilityCardError) as exc:
        mcp_client_tool_loop("cursor/composer-2.5")
    assert exc.value.reason_code == "capability_card_missing"


@pytest.mark.parametrize(
    ("model", "expected_loop"),
    [
        ("google/gemini-3.5-flash", True),
        ("google/gemini-3.6-flash", True),
        ("google/gemini-3-pro", True),
        ("google/gemini-3.1-pro", True),
        ("google/gemini-2.5-pro", True),
        ("xai/grok-4.5", True),
        ("xai/grok-4.3", True),
        ("openai/gpt-5.5", True),
        ("anthropic/claude-opus-4-8", True),
    ],
)
def test_f8_data_mirror_client_tool_loop(model: str, expected_loop: bool) -> None:
    assert mcp_client_tool_loop(model) is expected_loop


def test_anthropic_remote_connector_true() -> None:
    assert mcp_remote_connector("anthropic/claude-opus-4-8") is True


def test_openai_remote_connector_false() -> None:
    assert mcp_remote_connector("openai/gpt-5.5") is False


def test_server_side_tools_and_mount_backend() -> None:
    assert server_side_tools("xai/grok-4.5") == (
        "web_search",
        "x_search",
        "code_interpreter",
    )
    assert server_side_tools("openai/gpt-5.5") == ("web_search_preview",)
    assert server_side_tools("openai/o4-mini-deep-research") == (
        "web_search_preview",
    )
    assert server_side_tools("openai/o3-deep-research") == ("web_search_preview",)
    assert server_side_tools("perplexity/sonar-deep-research") == ()
    assert server_side_tools("anthropic/claude-opus-4-8") == ()
    assert server_side_tools("google/gemini-3.5-flash") == ()
    assert skills_mount_backend("openai/gpt-5.5") == "openai_container"
    assert skills_mount_backend("google/gemini-3.5-flash") == "none"


def _agents_yaml_roster_models() -> set[str]:
    repo_root = Path(__file__).resolve().parents[3]
    agents_path = repo_root / "config" / "agents.yaml"
    data = yaml.safe_load(agents_path.read_text(encoding="utf-8"))
    models: set[str] = set()
    for profile in (data.get("profiles") or {}).values():
        default = profile.get("default_model")
        if default:
            models.add(default)
        for allowed in profile.get("allowed_models") or []:
            models.add(allowed)
    for role in (data.get("roles") or {}).values():
        default = role.get("default_model")
        if default:
            models.add(default)
        for allowed in role.get("allowed_models") or []:
            models.add(allowed)
    return models


def test_roster_models_hit_card_keys_or_are_excluded() -> None:
    """Key parity: roster frontier models must hit a card; cursor/search-api excluded."""
    excluded_prefixes = ("cursor/",)
    excluded_exact = {"openai/gpt-5-search-api"}
    for model in _agents_yaml_roster_models():
        normalized = ModelId.parse(model).normalized
        if normalized in excluded_exact or normalized.startswith(excluded_prefixes):
            continue
        assert normalized in MODEL_CAPABILITY_CARDS, f"missing card for {normalized!r}"


def test_card_keys_use_normalized_form() -> None:
    for key in MODEL_CAPABILITY_CARDS:
        assert key == ModelId.parse(key).normalized


_DEEP_RESEARCH_DISCOVERY_MODELS = (
    "openai/o4-mini-deep-research",
    "openai/o3-deep-research",
    "perplexity/sonar-deep-research",
)


@pytest.mark.parametrize("model", _DEEP_RESEARCH_DISCOVERY_MODELS)
def test_deep_research_discovery_models_have_capability_cards(model: str) -> None:
    card = capability_card(model)
    assert card.mcp_client_tool_loop is False
    assert card.mcp_remote_connector is False
    assert inline_only(model) is True
    assert mcp_capable(model) is False


def test_deep_research_openrouter_routing_resolves_same_card() -> None:
    routed = capability_card("openrouter/perplexity/sonar-deep-research")
    direct = capability_card("perplexity/sonar-deep-research")
    assert routed == direct


def test_deep_research_card_keys_present_in_registry() -> None:
    for key in _DEEP_RESEARCH_DISCOVERY_MODELS:
        assert key in MODEL_CAPABILITY_CARDS


def test_deep_research_models_admit_without_card_missing() -> None:
    from agent_seat.profiles import client_side_mcp_tool_loop_admitted

    for model in _DEEP_RESEARCH_DISCOVERY_MODELS:
        assert client_side_mcp_tool_loop_admitted(model) is False
