"""Hermetic tests for effort submenu + qualified-radio recovery (a:31011 / a:30693)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.chat_model_effort import _effort_after_click
from claude_bundles.chat_model_select import select_from_ui


@pytest.mark.offline
@pytest.mark.asyncio
async def test_high_to_max_recovers_via_qualified_radio() -> None:
    page = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    apply_miss = {
        "ok": False,
        "step": "effort_failed",
        "after": "Model: Fable 5 High",
        "effort": {"ok": False, "step": "effort_trigger_missing"},
        "matched": "Fable 5",
    }
    recover_ok = {
        "ok": True,
        "step": "recovered_via_qualified_radio",
        "effort": {
            "ok": True,
            "step": "effort_set_via_qualified_radio",
            "level": "max",
        },
        "current_model": "Model: Fable 5 Max",
        "matched": "Fable 5 Max",
        "available_models": ["Fable 5 Max"],
    }
    with (
        patch(
            "claude_bundles.chat_model_effort._apply_effort",
            new_callable=AsyncMock,
            return_value=apply_miss,
        ),
        patch(
            "claude_bundles.chat_model_effort._recover_effort_via_qualified_radio",
            new_callable=AsyncMock,
            return_value=recover_ok,
        ),
        patch(
            "claude_bundles.chat_model_select.current_model_label",
            new_callable=AsyncMock,
            return_value="Model: Fable 5 High",
        ),
    ):
        result, err = await _effort_after_click(
            page,
            requested="fable-5-max",
            effort="max",
            matched="Fable 5",
            before="Model: Fable 5 High",
        )
    assert err is None
    assert result is not None
    assert result["step"] == "recovered_via_qualified_radio"
    assert result["ok"] is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_opus_high_to_fable_max_recovers() -> None:
    page = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    apply_miss = {
        "ok": False,
        "step": "effort_failed",
        "after": "Model: Opus 5 High",
        "effort": {"ok": False, "step": "effort_trigger_missing"},
        "matched": "Fable 5",
    }
    recover_ok = {
        "ok": True,
        "step": "recovered_via_qualified_radio",
        "effort": {
            "ok": True,
            "step": "effort_set_via_qualified_radio",
            "level": "max",
        },
        "current_model": "Model: Fable 5 Max",
        "matched": "Fable 5 Max",
        "available_models": ["Fable 5 Max"],
    }
    with (
        patch(
            "claude_bundles.chat_model_effort._apply_effort",
            new_callable=AsyncMock,
            return_value=apply_miss,
        ),
        patch(
            "claude_bundles.chat_model_effort._recover_effort_via_qualified_radio",
            new_callable=AsyncMock,
            return_value=recover_ok,
        ),
        patch(
            "claude_bundles.chat_model_select.current_model_label",
            new_callable=AsyncMock,
            return_value="Model: Opus 5 High",
        ),
    ):
        result, err = await _effort_after_click(
            page,
            requested="fable-5-max",
            effort="max",
            matched="Fable 5",
            before="Model: Opus 5 High",
        )
    assert err is None
    assert result is not None
    assert result["step"] == "recovered_via_qualified_radio"
    assert result["ok"] is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_attested_high_skips_submenu() -> None:
    page = AsyncMock()
    with (
        patch(
            "claude_bundles.chat_model_select.current_model_label",
            new_callable=AsyncMock,
            return_value="Model: Opus 5 High",
        ),
        patch(
            "claude_bundles.chat_model_select._open_picker",
            new_callable=AsyncMock,
        ) as open_picker,
        patch(
            "claude_bundles.chat_model_select._click_family_radio",
            new_callable=AsyncMock,
        ) as family_click,
        patch(
            "claude_bundles.chat_model_select.set_effort",
            new_callable=AsyncMock,
        ) as set_effort_mock,
    ):
        result = await select_from_ui(page, "opus-5", effort="high")
    assert result["step"] == "already_selected"
    open_picker.assert_not_awaited()
    family_click.assert_not_awaited()
    set_effort_mock.assert_not_awaited()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_honest_fail_when_qualified_radio_absent() -> None:
    page = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    apply_miss = {
        "ok": False,
        "step": "effort_failed",
        "after": "Model: Fable 5 High",
        "effort": {"ok": False, "step": "effort_trigger_missing"},
        "matched": "Fable 5",
    }
    recover_fail = {
        "ok": False,
        "step": "effort_failed",
        "after": "Model: Fable 5 High",
        "effort": {"ok": False, "step": "effort_trigger_missing"},
        "matched": "Fable 5",
        "before": "Model: Fable 5 High",
        "available_models": ["Fable 5"],
    }
    with (
        patch(
            "claude_bundles.chat_model_effort._apply_effort",
            new_callable=AsyncMock,
            return_value=apply_miss,
        ),
        patch(
            "claude_bundles.chat_model_effort._recover_effort_via_qualified_radio",
            new_callable=AsyncMock,
            return_value=recover_fail,
        ),
        patch(
            "claude_bundles.chat_model_select.current_model_label",
            new_callable=AsyncMock,
            return_value="Model: Fable 5 High",
        ),
    ):
        result, err = await _effort_after_click(
            page,
            requested="fable-5-max",
            effort="max",
            matched="Fable 5",
            before="Model: Fable 5 High",
        )
    assert result is None
    assert err is not None
    assert err["step"] == "effort_failed"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_predicted_family_click_skipped_when_effort_differs() -> None:
    page = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    family_click = AsyncMock()
    discover = AsyncMock(return_value=("Fable 5 Max", ["Fable 5 Max"], None))
    effort_after = AsyncMock(
        return_value=({"ok": True, "step": "effort_set", "level": "max"}, None)
    )
    with (
        patch(
            "claude_bundles.chat_model_select.current_model_label",
            new_callable=AsyncMock,
            side_effect=["Model: Fable 5 High", "Model: Fable 5 Max"],
        ),
        patch(
            "claude_bundles.chat_model_select._ensure_picker",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "claude_bundles.chat_model_select._open_picker",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.chat_model_select._expand_more_models",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.chat_model_select.list_picker_radios",
            new_callable=AsyncMock,
            return_value=["Opus 5", "Sonnet 5", "Haiku 4.5", "Fable 5"],
        ),
        patch(
            "claude_bundles.chat_model_select._click_family_radio",
            family_click,
        ),
        patch(
            "claude_bundles.chat_model_select._discover_and_click",
            discover,
        ),
        patch(
            "claude_bundles.chat_model_select._effort_after_click",
            effort_after,
        ),
    ):
        result = await select_from_ui(page, "fable-5-max", effort="max")
    family_click.assert_not_awaited()
    discover.assert_awaited()
    assert result["ok"] is True


@pytest.mark.offline
def test_set_effort_still_exported_from_select() -> None:
    from claude_bundles.chat_model_effort import set_effort as origin
    from claude_bundles.chat_model_select import set_effort as exported

    assert exported is origin
