"""Hermetic tests for on-demand CSE URL open during harvest."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.cse_session_harvest_open import harvest_by_opening_url
from cdp_ask.cse_session_models import HarvestRequest, HarvestResponse
from cdp_ask.followup_reattach import ReattachOutcome


@pytest.mark.asyncio
async def test_open_failed_stays_not_attached() -> None:
    failed = ReattachOutcome(ok=False, error="reattach_navigate_failed")
    harvest_page = AsyncMock()
    with (
        patch(
            "cdp_ask.cse_session_harvest_open.ensure_cse_attached",
            AsyncMock(return_value=failed),
        ),
        patch(
            "cdp_ask.cse_session_harvest_open._teardown_opened",
            AsyncMock(),
        ) as teardown,
    ):
        result = await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            None,
            harvest_page,
        )
    harvest_page.assert_not_awaited()
    teardown.assert_not_awaited()
    assert result.outcome == "not_attached"
    assert result.reason == "reattach_navigate_failed"


@pytest.mark.asyncio
async def test_open_success_scrapes_then_tears_down() -> None:
    page = MagicMock()
    opened = ReattachOutcome(ok=True, page=page, pw=MagicMock(), relaunched=True)
    async def _page(_page, _req, provenance=None):
        return HarvestResponse(outcome="harvested", provenance=provenance)

    harvest_page = AsyncMock(side_effect=_page)
    with (
        patch(
            "cdp_ask.cse_session_harvest_open.ensure_cse_attached",
            AsyncMock(return_value=opened),
        ),
        patch(
            "cdp_ask.cse_session_harvest_open._teardown_opened",
            AsyncMock(),
        ) as teardown,
    ):
        result = await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            {"k": 1},
            harvest_page,
        )
    harvest_page.assert_awaited_once()
    teardown.assert_awaited_once()
    assert result.outcome == "harvested"
    assert result.provenance and result.provenance.get("opened_on_demand") is True
