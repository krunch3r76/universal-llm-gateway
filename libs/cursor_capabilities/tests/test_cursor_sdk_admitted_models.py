"""Unit tests for cursor-sdk pair allowlist (AC 1–3)."""

from __future__ import annotations

import cursor_capabilities.cursor_capabilities as cap_mod
import pytest
from cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    CURSOR_SDK_ADMITTED_MODELS,
    cursor_sdk_model_set_fix_hint,
    is_cursor_sdk_model_admitted,
)
from cursor_capabilities.model_pools import OTHER_MODELS_BARE


def test_cursor_sdk_admitted_models_constant() -> None:
    assert CURSOR_SDK_ADMITTED_MODELS == frozenset({"composer-2.5", "grok-4.7"})
    assert CURSOR_SDK_ADMITTED_MODELS <= set(CURSOR_MODEL_CAPABILITIES.keys())


@pytest.mark.parametrize(
    "model",
    [
        "cursor/composer-2.5",
        "composer-2.5",
        "cursor/COMPOSER-2.5",
        "cursor/grok-4.7",
        "grok-4.7",
    ],
)
def test_is_cursor_sdk_model_admitted_members(model: str) -> None:
    assert is_cursor_sdk_model_admitted(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "gemini-4-argon",
        "cursor/Gemini-4-Argon",
        "cursor/auto",
        "auto",
        "cursor/claude-sonnet-5",
        "cursor/composer-2.5-fast",
        "cursor/opus-5.5",
        "anthropic/claude-sonnet-5-5",
        "",
    ],
)
def test_is_cursor_sdk_model_admitted_non_members(model: str) -> None:
    assert is_cursor_sdk_model_admitted(model) is False


def test_is_cursor_sdk_model_admitted_foreign_provider_no_raise() -> None:
    assert is_cursor_sdk_model_admitted("anthropic/claude-sonnet-5-5") is False


def test_monkeypatch_admitted_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cap_mod,
        "CURSOR_SDK_ADMITTED_MODELS",
        frozenset({"claude-sonnet-5"}),
    )
    assert is_cursor_sdk_model_admitted("cursor/claude-sonnet-5") is True
    assert is_cursor_sdk_model_admitted("cursor/composer-2.5") is False


def test_fix_hint_names_both_pair_ids() -> None:
    hint = cursor_sdk_model_set_fix_hint()
    assert "cursor/composer-2.5" in hint
    assert "cursor/grok-4.7" in hint


def test_explicit_pin_admits_other_models_pool() -> None:
    assert (
        is_cursor_sdk_model_admitted("cursor/claude-opus-5-5", explicit_pin=True)
        is True
    )
    assert is_cursor_sdk_model_admitted("cursor/claude-opus-5-5") is False


def test_explicit_pin_admits_all_other_models_pool_including_advisor() -> None:
    assert (
        is_cursor_sdk_model_admitted("cursor/gemini-4-argon", explicit_pin=True) is True
    )
    assert is_cursor_sdk_model_admitted("cursor/gemini-4-argon") is False


@pytest.mark.parametrize("bare_id", sorted(OTHER_MODELS_BARE))
def test_explicit_pin_admits_every_other_models_pool_card(bare_id: str) -> None:
    assert is_cursor_sdk_model_admitted(f"cursor/{bare_id}", explicit_pin=True) is True
    assert is_cursor_sdk_model_admitted(f"cursor/{bare_id}") is False
