"""Slice-2 tests for catalog-driven cursor model admission."""

from __future__ import annotations

import pytest

import cursor_capabilities.cursor_capabilities as cap_mod
from cursor_capabilities import CURSOR_DENIED_MODELS, catalog_divergences
from services.git_integration_worker.cursor_models import resolve_cursor


def test_resolve_cursor_admits_descriptor_unknown_with_empty_params() -> None:
    cfg = resolve_cursor("cursor/brand-new-model")
    assert cfg.model_id == "brand-new-model"
    assert cfg.params == ()


def test_resolve_cursor_still_returns_descriptor_knobs() -> None:
    cfg = resolve_cursor("cursor/composer-2.5")
    assert cfg.model_id == "composer-2.5"
    assert {spec.name for spec in cfg.params} == {"fast"}


def test_resolve_cursor_grok_omit_fast_true() -> None:
    """Grok 4.7 omit-path emits context=256k and fast=true.

    ListModels' default variant is context=500k. The card pins 256k.
    """
    from cursor_capabilities import default_variant, supported_knobs
    from services.git_integration_worker.cursor_models import (
        build_model_selection,
        selected_context_window_tokens,
    )

    cfg = resolve_cursor("cursor/grok-4.7")
    assert cfg.model_id == "grok-4.7"
    assert {spec.name for spec in cfg.params} == {"context", "effort", "fast"}
    assert default_variant("grok-4.7") == {
        "context": "256k",
        "effort": "high",
        "fast": "true",
    }
    assert supported_knobs("grok-4.7")["context"].accepted == ("256k", "500k")
    assert supported_knobs("grok-4.7")["context"].default == "256k"
    assert supported_knobs("grok-4.7")["fast"].default == "true"
    selection = build_model_selection(cfg)
    by_id = {p.id: p.value for p in selection.params}
    assert by_id["context"] == "256k"
    assert by_id["fast"] == "true"
    assert by_id["reasoning_effort"] == "high"
    pinned = build_model_selection(cfg, {"context": "500k"})
    assert {p.id: p.value for p in pinned.params}["context"] == "500k"
    assert selected_context_window_tokens("cursor/grok-4.7", by_id) == 256_000
    assert (
        selected_context_window_tokens("cursor/grok-4.7", {"context": "500k"})
        == 500_000
    )


def test_resolve_cursor_denies_canonicalized_denylist_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cap_mod,
        "CURSOR_DENIED_MODELS",
        frozenset({"claude-sonnet-5"}),
    )
    with pytest.raises(ValueError, match="denied"):
        resolve_cursor("cursor/Claude-Sonnet-5")


def test_catalog_divergences_detects_missing_descriptor_model() -> None:
    errors = catalog_divergences({})
    assert any("composer-2.5" in err for err in errors)
