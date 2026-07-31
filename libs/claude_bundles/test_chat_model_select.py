"""Hermetic tests for UI-discovery model matching (friction a24692)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.chat_model_select import (
    PREDICTED_MODEL_LABELS,
    family_pattern,
    label_satisfies_request,
    match_model_request,
    parse_model_request,
    select_fable_5,
)


def test_parse_strips_effort_tokens() -> None:
    assert parse_model_request("opus-5-extra") == ("opus-5", "extra")
    assert parse_model_request("opus-5-high") == ("opus-5", "high")
    assert parse_model_request("sonnet-5") == ("sonnet-5", None)
    assert parse_model_request("leave") == ("leave", None)


def test_family_pattern_matches_live_ui_labels() -> None:
    assert family_pattern("sonnet-5").search("Sonnet 5")
    assert family_pattern("sonnet-5").search("Sonnet 5 High")
    assert family_pattern("opus-5").search("Opus 5 Extra")
    assert family_pattern("fable-5").search("Fable 5")
    assert not family_pattern("sonnet-5").search("Opus 5")


def test_match_model_request_discovers_sonnet_without_allowlist() -> None:
    """Falsifier for a24692 — Sonnet in live picker must match; no code whitelist."""
    labels = [
        "Opus 5",
        "Sonnet 5",
        "Haiku 4.5",
        "More models",  # non-radio noise tolerated if present in list
    ]
    assert match_model_request("sonnet-5", labels) == "Sonnet 5"
    assert match_model_request("opus-5", labels) == "Opus 5"
    assert match_model_request("fable-5", labels) is None


def test_prediction_list_is_try_first_not_availability_gate() -> None:
    """Predicted labels cover common SKUs; unknown names still discover via UI."""
    assert match_model_request("sonnet-5", list(PREDICTED_MODEL_LABELS)) == "Sonnet 5"
    assert match_model_request("opus-5", list(PREDICTED_MODEL_LABELS)) == "Opus 5"
    assert "Sonnet 5" in PREDICTED_MODEL_LABELS
    # Not in prediction list ⇒ None here; select_from_ui falls through to live radios.
    assert match_model_request("glorp-9", list(PREDICTED_MODEL_LABELS)) is None


def test_match_prefers_tighter_family_radio() -> None:
    labels = ["Opus 5 Extra", "Opus 5"]
    assert match_model_request("opus-5", labels) == "Opus 5"


def test_label_satisfies_request_effort_gates() -> None:
    assert label_satisfies_request("opus-5", "Opus 5 High", effort="high")
    assert not label_satisfies_request("opus-5", "Opus 5 Extra", effort="high")
    assert label_satisfies_request("opus-5", "Opus 5 Extra", effort="extra")
    assert not label_satisfies_request("opus-5", "Opus 5 High", effort="extra")
    assert label_satisfies_request("sonnet-5", "Sonnet 5", effort=None)
    assert label_satisfies_request("sonnet-5", "Sonnet 5 High", effort=None)


@pytest.mark.asyncio
async def test_select_fable_5_delegates_to_select_model() -> None:
    page = object()
    with patch(
        "claude_bundles.chat_model_select.select_model",
        new_callable=AsyncMock,
        return_value={"ok": True},
    ) as select_model:
        result = await select_fable_5(page)
    select_model.assert_awaited_once_with(page, "fable-5")
    assert result == {"ok": True}
