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


@pytest.mark.asyncio
async def test_loading_incomplete_skips_park_for_minted_lane() -> None:
    page = MagicMock()
    pw = MagicMock()
    opened = ReattachOutcome(
        ok=True,
        page=page,
        pw=pw,
        lane_created=True,
        registration_id="reg-new",
    )
    harvest_page = AsyncMock(
        return_value=HarvestResponse(
            outcome="incomplete_dom",
            reason="loading",
        )
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest_open.ensure_cse_attached",
            AsyncMock(return_value=opened),
        ),
        patch(
            "cdp_ask.cse_session_harvest_open._teardown_attempt",
            AsyncMock(),
        ) as teardown,
        patch(
            "cdp_ask.cse_session_harvest_open.cdp_registry.deregister_lane",
        ) as deregister,
        patch(
            "cdp_ask.cse_session_harvest_open.park_relaunched_host",
            AsyncMock(),
        ) as park,
    ):
        result = await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            None,
            harvest_page,
        )
    teardown.assert_awaited_once_with(page, pw, close_page=False)
    deregister.assert_not_called()
    park.assert_not_awaited()
    assert result.outcome == "incomplete_dom"
    assert result.reason == "loading"


@pytest.mark.asyncio
async def test_loading_incomplete_borrowed_host_still_closes() -> None:
    page = MagicMock()
    pw = MagicMock()
    opened = ReattachOutcome(
        ok=True,
        page=page,
        pw=pw,
        lane_created=False,
        relaunched=False,
    )
    harvest_page = AsyncMock(
        return_value=HarvestResponse(
            outcome="incomplete_dom",
            reason="loading",
        )
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest_open.ensure_cse_attached",
            AsyncMock(return_value=opened),
        ),
        patch(
            "cdp_ask.cse_session_harvest_open._teardown_attempt",
            AsyncMock(),
        ) as teardown,
    ):
        await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            None,
            harvest_page,
        )
    teardown.assert_awaited_once_with(page, pw)


@pytest.mark.asyncio
async def test_skip_park_keeps_registration_for_lane_order() -> None:
    """Skip-teardown on loading mint leaves registration live for _lane_order."""
    from cdp_ask.followup_reattach import _lane_order

    page = MagicMock()
    pw = MagicMock()
    reg_id = "reg-mint-loading"
    opened = ReattachOutcome(
        ok=True,
        page=page,
        pw=pw,
        lane_created=True,
        registration_id=reg_id,
    )
    harvest_page = AsyncMock(
        return_value=HarvestResponse(outcome="incomplete_dom", reason="loading")
    )
    lane = __import__("claude_bundles.cdp_registry", fromlist=["cdp_registry"]).Registration(
        registration_id=reg_id,
        port=9222,
        profile_suffix="x",
        profile=__import__("pathlib").Path("/tmp/p"),
        cdp_url="http://127.0.0.1:9222",
        holder="cse-session-harvest",
        purpose="ask",
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest_open.ensure_cse_attached",
            AsyncMock(return_value=opened),
        ),
        patch(
            "cdp_ask.cse_session_harvest_open._teardown_attempt",
            AsyncMock(),
        ),
        patch(
            "cdp_ask.cse_session_harvest_open.cdp_registry.deregister_lane",
        ) as deregister,
    ):
        await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            None,
            harvest_page,
        )
    deregister.assert_not_called()
    ordered = _lane_order([lane], None, "https://claude.ai/cowork/cse_x")
    assert ordered[0].registration_id == reg_id
