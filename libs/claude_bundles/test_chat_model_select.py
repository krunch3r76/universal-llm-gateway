"""Hermetic tests for UI-discovery model matching (friction a24692)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.chat_model_select import (
    PREDICTED_MODEL_LABELS,
    _click_family_radio,
    family_pattern,
    label_satisfies_request,
    match_model_request,
    parse_model_request,
    select_fable_5_1,
    select_from_ui,
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
async def test_select_fable_5_1_delegates_to_select_model() -> None:
    page = object()
    with patch(
        "claude_bundles.chat_model_select.select_model",
        new_callable=AsyncMock,
        return_value={"ok": True},
    ) as select_model:
        result = await select_fable_5_1(page)
    select_model.assert_awaited_once_with(page, "fable-5.1")
    assert result == {"ok": True}


_GLUED_FABLE = "Fable 5.1For your toughest challenges"
_FABLE_MENU = [
    _GLUED_FABLE,
    "Fable 5",
    "Opus 5.5Most capable for ambitious work",
    "Opus 4.6",
    "Sonnet 4.6",
]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_glued_fable_hit_that_only_changes_opus_effort_is_not_success() -> None:
    """Chip after a bad click stays Opus. That is not a Fable select."""
    page = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    effort_after = AsyncMock()
    with (
        patch(
            "claude_bundles.chat_model_select.current_model_label",
            new_callable=AsyncMock,
            side_effect=["Model: Opus 5.5 Medium", "Model: Opus 5.5 High"],
        ),
        patch(
            "claude_bundles.chat_model_select._ensure_picker",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("claude_bundles.chat_model_select._open_picker", new_callable=AsyncMock),
        patch(
            "claude_bundles.chat_model_select._expand_more_models",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.chat_model_select.list_picker_radios",
            new_callable=AsyncMock,
            return_value=list(_FABLE_MENU),
        ),
        patch(
            "claude_bundles.chat_model_select._click_family_radio",
            new_callable=AsyncMock,
            return_value=_GLUED_FABLE,
        ),
        patch(
            "claude_bundles.chat_model_select._effort_after_click",
            effort_after,
        ),
    ):
        result = await select_from_ui(page, "fable-5.1", effort="high")
    assert result["ok"] is False
    assert result["step"] == "select_no_attest"
    assert result["before"] == "Model: Opus 5.5 Medium"
    assert result["after"] == "Model: Opus 5.5 High"
    assert result["matched"] == _GLUED_FABLE
    assert result["requested"] == "fable-5.1"
    assert result["path"] == "discover"
    assert result["source"] == "cdp.model_select"
    assert result["menu_label_glued"] is True
    assert result["matched_is_chip"] is False
    assert result["effort_only_chip_change"] is True
    assert result["as_of"]
    effort_after.assert_not_awaited()
    assert label_satisfies_request("fable-5.1", result["after"], effort="high") is False


@pytest.mark.offline
@pytest.mark.asyncio
async def test_fable_name_click_attests_before_effort() -> None:
    page = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    effort_after = AsyncMock(
        return_value=({"ok": True, "step": "effort_set", "level": "high"}, None)
    )
    with (
        patch(
            "claude_bundles.chat_model_select.current_model_label",
            new_callable=AsyncMock,
            side_effect=[
                "Model: Opus 5.5 Medium",
                "Model: Fable 5.1",
                "Model: Fable 5.1 High",
            ],
        ),
        patch(
            "claude_bundles.chat_model_select._ensure_picker",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("claude_bundles.chat_model_select._open_picker", new_callable=AsyncMock),
        patch(
            "claude_bundles.chat_model_select._expand_more_models",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.chat_model_select.list_picker_radios",
            new_callable=AsyncMock,
            return_value=list(_FABLE_MENU),
        ),
        patch(
            "claude_bundles.chat_model_select._click_family_radio",
            new_callable=AsyncMock,
            return_value="Fable 5.1",
        ),
        patch(
            "claude_bundles.chat_model_select._effort_after_click",
            effort_after,
        ),
    ):
        result = await select_from_ui(page, "fable-5.1", effort="high")
    assert result["ok"] is True
    assert result["current_model"] == "Model: Fable 5.1 High"
    effort_after.assert_awaited()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_click_family_radio_picks_name_not_parent() -> None:
    rows = [
        {
            "own": (
                "Opus 5.5Most capable for ambitious work"
                "Fable 5.1For your toughest challenges"
            ),
            "full": "parent",
        },
        {
            "own": _GLUED_FABLE,
            "full": _GLUED_FABLE,
        },
        {"own": "Fable 5.1", "full": "Fable 5.1"},
    ]
    clicks: list[int] = []

    async def _evaluate(_script: str, arg: int | None = None) -> object:
        if arg is None:
            return rows
        clicks.append(arg)
        return True

    page = AsyncMock()
    page.evaluate = AsyncMock(side_effect=_evaluate)
    page.wait_for_timeout = AsyncMock()
    label = await _click_family_radio(page, "fable-5.1")
    assert label == "Fable 5.1"
    assert clicks == [2]
