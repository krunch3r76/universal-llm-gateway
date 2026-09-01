"""Tests for Cursor Other Models pool membership."""

from __future__ import annotations

import pytest
from cursor_capabilities import is_other_models_pool
from cursor_capabilities.model_pools import OTHER_MODELS_BARE


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-sonnet-5",
        "cursor/claude-sonnet-5",
        "cursor/claude-opus-5",
        "cursor/gpt-5.6-terra",
        "cursor/gpt-5.6-luna",
        "cursor/claude-fable-5",
    ],
)
def test_other_models_pool_membership(model_id: str) -> None:
    assert is_other_models_pool(model_id)


@pytest.mark.parametrize(
    "model_id",
    ["cursor/grok-4.6", "cursor/composer-2.5", "grok-4.6", "composer-2.5"],
)
def test_cursor_models_pool_not_other_models(model_id: str) -> None:
    assert not is_other_models_pool(model_id)


@pytest.mark.parametrize(
    "model_id",
    ["", None, "anthropic/claude-sonnet-5", "not-a-cursor-model", "openai/gpt-5"],
)
def test_unknown_or_foreign_id_returns_false(model_id: str | None) -> None:
    assert not is_other_models_pool(model_id)


def test_luna_in_static_set() -> None:
    assert "gpt-5.6-luna" in OTHER_MODELS_BARE
