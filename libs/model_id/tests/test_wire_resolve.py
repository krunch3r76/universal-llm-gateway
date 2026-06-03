"""Tests for bare cloud model id → provider/model wire resolution."""

import pytest
from model_id.wire_resolve import (
    WireModelResolutionError,
    require_cloud_provider,
    resolve_wire_model_id,
)


def test_bare_gpt_resolves_to_openai() -> None:
    out = resolve_wire_model_id("gpt-5.5", require_cloud=True)
    assert out.wire_id == "openai/gpt-5.5"
    assert out.provider == "openai"
    assert out.was_bare is True


def test_prefixed_id_passthrough() -> None:
    out = resolve_wire_model_id("anthropic/claude-sonnet-4-6", require_cloud=True)
    assert out.wire_id == "anthropic/claude-sonnet-4-6"
    assert out.provider == "anthropic"
    assert out.was_bare is False


def test_bare_gemini_resolves_to_google() -> None:
    out = resolve_wire_model_id("gemini-3.5-flash", require_cloud=True)
    assert out.wire_id == "google/gemini-3.5-flash"
    assert out.provider == "google"


def test_unknown_bare_rejected() -> None:
    with pytest.raises(WireModelResolutionError, match="cannot be routed"):
        resolve_wire_model_id("totally-unknown-model", require_cloud=True)


def test_local_id_rejected_when_require_cloud() -> None:
    local = "hermes-3-llama-3.1-8b-uncensored-16384-hybrid"
    with pytest.raises(WireModelResolutionError, match="local gateway"):
        resolve_wire_model_id(local, require_cloud=True)


def test_require_cloud_provider_rejects_none() -> None:
    with pytest.raises(WireModelResolutionError, match="no provider prefix"):
        require_cloud_provider(None, model="gpt-5.5")


def test_effort_suffix_stripped_before_inference() -> None:
    out = resolve_wire_model_id("gpt-5.5__effort_high", require_cloud=True)
    assert out.wire_id == "openai/gpt-5.5"


def test_bare_cloud_prefix_before_local_heuristic() -> None:
    """Cloud suffixes like ``chat`` must not trip the local-id heuristic."""
    out = resolve_wire_model_id("gpt-5-chat", require_cloud=True)
    assert out.wire_id == "openai/gpt-5-chat"
    assert out.provider == "openai"
    assert out.was_bare is True
