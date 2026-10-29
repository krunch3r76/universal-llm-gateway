"""Offline tests for claude.ai/code model selection (R17 / friction:40618)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.project_ask import (
    _apply_code_surface_model_picker,
    _compose_model_selected,
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
            loc.first.get_attribute = AsyncMock(
                side_effect=lambda _n: toolbar["label"]
            )
            loc.first.inner_text = AsyncMock(return_value="")
            loc.first.click = AsyncMock()
            return loc
        if sel == '[role="menu"]':
            loc.count = AsyncMock(return_value=1)
            return loc
        if sel == '[role="menuitemradio"]':
            loc.count = AsyncMock(return_value=len(labels))

            class _Nth:
                def __init__(self, i: int):
                    self._i = i

                async def inner_text(self) -> str:
                    return labels[self._i]

            loc.nth = MagicMock(side_effect=lambda i: _Nth(i))
            return loc
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    async def _pick_sonnet(**_kwargs: object) -> None:
        toolbar["label"] = "Model: Sonnet 5.5 Medium"

    sonnet_radio = MagicMock()
    sonnet_radio.count = AsyncMock(return_value=1)
    sonnet_radio.first.click = AsyncMock(side_effect=_pick_sonnet)
    page.get_by_role = MagicMock(return_value=sonnet_radio)

    with patch(
        "claude_bundles.project_ask.current_model_label",
        new=AsyncMock(return_value=""),
    ):
        result = await _apply_code_surface_model_picker(page, "sonnet-5.5-medium")

    assert result["ok"] is True
    assert result["step"] == "code_surface_model_picked"
    sonnet_radio.first.click.assert_awaited_once()


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
