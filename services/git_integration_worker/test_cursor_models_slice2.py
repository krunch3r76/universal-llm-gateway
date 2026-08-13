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


def test_resolve_cursor_grok_45_matches_live_catalog() -> None:
    """Grok 4.6 cursor-sdk knobs match live ListModels (effort+fast; default fast=true)."""
    from cursor_capabilities import default_variant, supported_knobs
    from services.git_integration_worker.cursor_models import build_model_selection

    cfg = resolve_cursor("cursor/grok-4.6")
    assert cfg.model_id == "grok-4.6"
    assert {spec.name for spec in cfg.params} == {"effort", "fast"}
    assert default_variant("grok-4.6") == {"effort": "high", "fast": "true"}
    assert supported_knobs("grok-4.6")["fast"].default == "true"
    selection = build_model_selection(cfg)
    by_id = {p.id: p.value for p in selection.params}
    assert by_id["fast"] == "true"
    assert by_id["effort"] == "high"


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
