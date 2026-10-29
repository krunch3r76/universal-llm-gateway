"""Offline tests for claude.ai/code model selection (R17 / friction:40618)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.chat_model_match import match_model_request
from claude_bundles.project_ask import (
    _apply_code_surface_model_picker,
    _assert_code_surface_model_before_send,
    _compose_model_selected,
    _effort_token_from_code_toolbar,
)

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_code_surface_compose_applies_menu_picker_not_chat_select_model() -> None:
    page = MagicMock()
    page.url = "https://claude.ai/code"

    async def _fake_goto(*_a, **_k):
        return None

    with (
        patch(
            "claude_bundles.project_ask.goto_fresh_compose",
            new=AsyncMock(side_effect=_fake_goto),
        ),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "claude_bundles.project_ask.select_model",
            new=AsyncMock(),
        ) as select_mock,
        patch(
            "claude_bundles.project_ask._apply_code_surface_model_picker",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "step": "code_surface_model_picked",
                    "current_model": "Model: Sonnet 5.5 Medium",
                }
            ),
        ) as apply_mock,
        patch(
            "claude_bundles.project_ask._read_code_surface_model_label",
            new=AsyncMock(return_value="Model: Sonnet 5.5 Medium"),
        ),
    ):
        info = await _compose_model_selected(
            page,
            "sonnet-5-5",
            compose_url="https://claude.ai/code",
            ensure_cowork_auto=False,
            claude_surface="code",
        )

    select_mock.assert_not_called()
    apply_mock.assert_awaited_once()
    assert info["ok"] is True
    assert info["step"] == "code_surface_page_model"
    assert info["model_ran"] == "Model: Sonnet 5.5 Medium"


@pytest.mark.asyncio
async def test_apply_code_surface_model_picker_clicks_menuitemradio() -> None:
    """R24 probe DOM: menuitemradio rows Opus/Sonnet/Haiku/Fable under role=menu."""
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    labels = ["Opus 5.5", "Sonnet 5.5", "Haiku 5.5"]
    toolbar = {"label": "Model: Opus 5.5 Medium"}

    def _locator(sel: str):
        loc = MagicMock()
        if 'aria-label*="Model"' in sel:
            loc.count = AsyncMock(return_value=1)
            loc.first.get_attribute = AsyncMock(side_effect=lambda _n: toolbar["label"])
            loc.first.inner_text = AsyncMock(return_value="")
            loc.first.click = AsyncMock()
            return loc
        if sel == '[role="menu"]':
            loc.count = AsyncMock(return_value=1)
            return loc
            loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    menu_rows = [{"own": name, "full": name} for name in labels]

    async def _evaluate(script: str, arg: object | None = None) -> object:
        if arg is None:
            return menu_rows
        if isinstance(arg, int) and arg == 1:
            toolbar["label"] = "Model: Sonnet 5.5 Medium"
            return True
        return False

    page.evaluate = AsyncMock(side_effect=_evaluate)
    page.get_by_role = MagicMock()

    set_effort = AsyncMock(return_value=True)
    with (
        patch(
            "claude_bundles.project_ask.current_model_label",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "claude_bundles.project_ask._code_surface_set_effort_from_menu",
            new=set_effort,
        ),
    ):
        result = await _apply_code_surface_model_picker(page, "sonnet-5.5-medium")

    assert result["ok"] is True
    assert result["step"] == "code_surface_model_picked"
    assert page.evaluate.await_count >= 2
    set_effort.assert_awaited_once_with(page, "medium")


def test_effort_token_from_code_toolbar() -> None:
    assert _effort_token_from_code_toolbar("Model: Opus 5.5 Medium") == "medium"
    assert _effort_token_from_code_toolbar("Model: Sonnet 5.5 High") == "high"
    assert _effort_token_from_code_toolbar("Model: Opus 5.5 Max") == "max"


def test_assert_code_surface_uses_toolbar_effort_token() -> None:
    """R25: gate matches Effort submenu text (Medium), not sealed default (High) alone."""
    _assert_code_surface_model_before_send(
        "opus-5.5-medium",
        {
            "model_ran": "Model: Opus 5.5 Medium",
            "current_model": "Model: Opus 5.5 Medium",
        },
    )


@pytest.mark.parametrize(
    ("requested", "toolbar_label"),
    [
        ("opus-5.5-max", "Model: Opus 5.5 Medium"),
        ("opus-5.5-high", "Model: Opus 5.5 Medium"),
        ("opus-5.5-extra", "Model: Opus 5.5 Medium"),
        ("sonnet-5.5-extra", "Model: Sonnet 5.5 Medium"),
        ("sonnet-5-5", "Model: Opus 5.5 Medium"),
    ],
)
def test_assert_code_surface_effort_mismatch_fails_pre_send(
    requested: str, toolbar_label: str
) -> None:
    with pytest.raises(RuntimeError, match="model attestation mismatch"):
        _assert_code_surface_model_before_send(
            requested,
            {"model_ran": toolbar_label, "current_model": toolbar_label},
        )


@pytest.mark.asyncio
async def test_apply_code_surface_picker_fails_when_effort_menu_returns_false() -> None:
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    toolbar = {"label": "Model: Opus 5.5 Medium"}

    def _locator(sel: str):
        loc = MagicMock()
        if 'aria-label*="Model"' in sel:
            loc.count = AsyncMock(return_value=1)
            loc.first.get_attribute = AsyncMock(side_effect=lambda _n: toolbar["label"])
            loc.first.inner_text = AsyncMock(return_value="")
            loc.first.click = AsyncMock()
            return loc
        if sel == '[role="menu"]':
            loc.count = AsyncMock(return_value=1)
            return loc
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    menu_rows = [{"own": "Opus 5.5", "full": "Opus 5.5"}]

    async def _evaluate(script: str, arg: object | None = None) -> object:
        if arg is None:
            return menu_rows
        if isinstance(arg, int):
            toolbar["label"] = "Model: Opus 5.5 Medium"
            return True
        return False

    page.evaluate = AsyncMock(side_effect=_evaluate)
    page.get_by_role = MagicMock()

    with (
        patch(
            "claude_bundles.project_ask.current_model_label",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "claude_bundles.project_ask._code_surface_set_effort_from_menu",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = await _apply_code_surface_model_picker(page, "opus-5.5-max")

    assert result["ok"] is False
    assert result["step"] == "code_surface_effort_menu_failed"


@pytest.mark.asyncio
async def test_apply_code_surface_picker_hyphenated_sonnet_wire() -> None:
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    toolbar = {"label": "Model: Opus 5.5 Medium"}

    def _locator(sel: str):
        loc = MagicMock()
        if 'aria-label*="Model"' in sel:
            loc.count = AsyncMock(return_value=1)
            loc.first.get_attribute = AsyncMock(side_effect=lambda _n: toolbar["label"])
            loc.first.inner_text = AsyncMock(return_value="")
            loc.first.click = AsyncMock()
            return loc
        if sel == '[role="menu"]':
            loc.count = AsyncMock(return_value=1)
            return loc
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    menu_rows = [{"own": "Sonnet 5.5", "full": "Sonnet 5.5"}]

    async def _evaluate(script: str, arg: object | None = None) -> object:
        if arg is None:
            return menu_rows
        if isinstance(arg, int) and arg == 0:
            toolbar["label"] = "Model: Sonnet 5.5 Extra High"
            return True
        return False

    page.evaluate = AsyncMock(side_effect=_evaluate)
    page.get_by_role = MagicMock()

    with (
        patch(
            "claude_bundles.project_ask.current_model_label",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "claude_bundles.project_ask._code_surface_set_effort_from_menu",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await _apply_code_surface_model_picker(page, "sonnet-5-5")

    assert result["ok"] is True
    assert result["step"] == "code_surface_model_picked"


@pytest.mark.offline
def test_code_surface_opus_5_does_not_match_opus_5_5_menu() -> None:
    """AC2/AC3 — /code menu is 5.x SKUs; opus-5 must not alias to Opus 5.5."""
    menu = ["Opus 5.51", "Fable 5.12", "Sonnet 5.5", "Haiku 5.54"]
    assert match_model_request("opus-5", menu) is None
    assert match_model_request("opus-5.5", menu) == "Opus 5.51"


@pytest.mark.asyncio
async def test_apply_code_surface_opus_5_fails_on_four_item_menu() -> None:
    """cdp/opus-5 on /code fails fast (AC2) when menu lacks Opus 5."""
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    toolbar = {"label": "Model: Sonnet 5.5 Medium"}

    def _locator(sel: str):
        loc = MagicMock()
        if 'aria-label*="Model"' in sel:
            loc.count = AsyncMock(return_value=1)
            loc.first.get_attribute = AsyncMock(side_effect=lambda _n: toolbar["label"])
            loc.first.inner_text = AsyncMock(return_value="")
            loc.first.click = AsyncMock()
            return loc
        if sel == '[role="menu"]':
            loc.count = AsyncMock(return_value=1)
            return loc
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    menu = ["Opus 5.51", "Fable 5.12", "Sonnet 5.5", "Haiku 5.54"]
    menu_rows = [{"own": name, "full": name} for name in menu]

    async def _evaluate(script: str, arg: object | None = None) -> object:
        if arg is None:
            return menu_rows
        return False

    page.evaluate = AsyncMock(side_effect=_evaluate)

    with patch(
        "claude_bundles.project_ask.current_model_label",
        new=AsyncMock(return_value=""),
    ):
        result = await _apply_code_surface_model_picker(page, "cdp/opus-5-high")

    assert result["ok"] is False
    assert result["step"] == "code_surface_model_not_in_menu"
    assert result["requested"] == "opus-5"
    assert result["available_models"] == menu


@pytest.mark.asyncio
async def test_apply_code_surface_fails_not_in_menu_names_opus_not_fable() -> None:
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    toolbar = {"label": "Model: Sonnet 5.5 Medium"}

    def _locator(sel: str):
        loc = MagicMock()
        if 'aria-label*="Model"' in sel:
            loc.count = AsyncMock(return_value=1)
            loc.first.get_attribute = AsyncMock(side_effect=lambda _n: toolbar["label"])
            loc.first.inner_text = AsyncMock(return_value="")
            loc.first.click = AsyncMock()
            return loc
        if sel == '[role="menu"]':
            loc.count = AsyncMock(return_value=1)
            return loc
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    menu_rows = [{"own": "Sonnet 5.5", "full": "Sonnet 5.5"}]

    async def _evaluate(script: str, arg: object | None = None) -> object:
        if arg is None:
            return menu_rows
        return False

    page.evaluate = AsyncMock(side_effect=_evaluate)

    with patch(
        "claude_bundles.project_ask.current_model_label",
        new=AsyncMock(return_value=""),
    ):
        result = await _apply_code_surface_model_picker(page, "cdp/opus-5-high")

    assert result["ok"] is False
    assert result["step"] == "code_surface_model_not_in_menu"
    assert result["requested"] == "opus-5"


@pytest.mark.asyncio
async def test_cowork_surface_still_calls_select_model() -> None:
    page = MagicMock()
    page.url = "https://claude.ai/new"

    with (
        patch(
            "claude_bundles.project_ask.goto_fresh_compose",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "claude_bundles.project_ask.select_model",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "step": "already_selected",
                    "requested": "opus-5",
                }
            ),
        ) as select_mock,
    ):
        info = await _compose_model_selected(
            page,
            "opus-5",
            compose_url="https://claude.ai/new",
            claude_surface="cowork",
        )

    select_mock.assert_awaited_once()
    assert info["ok"] is True
