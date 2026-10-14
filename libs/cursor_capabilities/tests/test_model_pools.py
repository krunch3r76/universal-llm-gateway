"""Tests for Cursor Other Models pool membership."""

from __future__ import annotations

import pytest
from cursor_capabilities import CURSOR_MODEL_CAPABILITIES, is_other_models_pool
from cursor_capabilities.model_pools import (
    CURSOR_MODELS_BILLING_BARE,
    CURSOR_MODELS_POOL_BARE,
    OTHER_MODELS_BARE,
)


def test_primary_key_set_is_not_the_billing_label() -> None:
    assert "auto" in CURSOR_MODELS_POOL_BARE
    assert "auto" not in CURSOR_MODELS_BILLING_BARE
    assert CURSOR_MODELS_BILLING_BARE <= CURSOR_MODELS_POOL_BARE
    assert {"grok-4.6", "grok-4.5", "grok-4.7", "composer-2.5"} <= (
        CURSOR_MODELS_BILLING_BARE
    )


def test_other_models_bare_is_carded_minus_cursor_models_pool() -> None:
    expected = frozenset(CURSOR_MODEL_CAPABILITIES) - CURSOR_MODELS_POOL_BARE
    assert OTHER_MODELS_BARE == expected
    assert "gemini-4-argon" in OTHER_MODELS_BARE
    assert "gpt-5.6-luna" in OTHER_MODELS_BARE


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-sonnet-5",
        "cursor/claude-sonnet-5",
        "claude-sonnet-5-5",
        "cursor/claude-sonnet-5-5",
        "cursor/claude-opus-5",
        "cursor/claude-opus-5-5",
        "cursor/gpt-5.6-terra",
        "cursor/gpt-5.6-luna",
        "cursor/claude-fable-5",
        "cursor/gemini-4-argon",
        "gemini-3.6-flash",
    ],
)
def test_other_models_pool_membership(model_id: str) -> None:
    assert is_other_models_pool(model_id)


@pytest.mark.parametrize(
    "model_id",
    [
        "cursor/grok-4.7",
        "cursor/composer-2.5",
        "grok-4.7",
        "composer-2.5",
        "cursor/auto",
        "auto",
    ],
)
def test_cursor_models_pool_not_other_models(model_id: str) -> None:
    assert not is_other_models_pool(model_id)


@pytest.mark.parametrize(
    "model_id",
    ["", None, "anthropic/claude-sonnet-5", "not-a-cursor-model", "openai/gpt-5"],
)
def test_unknown_or_foreign_id_returns_false(model_id: str | None) -> None:
    assert not is_other_models_pool(model_id)
