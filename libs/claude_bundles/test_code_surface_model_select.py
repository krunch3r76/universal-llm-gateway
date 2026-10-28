"""Offline tests for claude.ai/code model selection (R17 / friction:40618)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.project_ask import _compose_model_selected

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_code_surface_skips_picker_and_records_page_model() -> None:
    page = MagicMock()
    page.url = "https://claude.ai/code"

    async def _fake_goto(*_a, **_k):
        return None

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first.get_attribute = AsyncMock(return_value="Model: Opus 5.5 Medium")
    btn.first.inner_text = AsyncMock(return_value="")
    page.locator = MagicMock(return_value=btn)

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
    ):
        info = await _compose_model_selected(
            page,
            "sonnet-5-5",
            compose_url="https://claude.ai/code",
            ensure_cowork_auto=False,
            claude_surface="code",
        )

    select_mock.assert_not_called()
    assert info["ok"] is True
    assert info["step"] == "code_surface_page_model"
    assert info["requested"] == "sonnet-5-5"
    assert info["model_ran"] == "Model: Opus 5.5 Medium"
    assert info["current_model"] == "Model: Opus 5.5 Medium"


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
