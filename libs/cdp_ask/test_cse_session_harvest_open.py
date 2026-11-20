"""Hermetic tests for on-demand CSE URL open during harvest."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.cse_session_harvest_open import _teardown_opened, harvest_by_opening_url
from cdp_ask.cse_session_models import HarvestRequest, HarvestResponse
from cdp_ask.followup_reattach import ReattachOutcome


@pytest.mark.asyncio
async def test_open_failed_stays_not_attached() -> None:
    failed = ReattachOutcome(ok=False, error="reattach_navigate_failed")
    harvest_page = AsyncMock()
    ensure = AsyncMock(return_value=failed)
    with (
        patch(
            "cdp_ask.cse_session_harvest_open.ensure_cse_attached",
            ensure,
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
    ensure.assert_awaited_once()
    harvest_page.assert_not_awaited()
    teardown.assert_not_awaited()
    assert result.outcome == "not_attached"
    assert result.reason == "reattach_navigate_failed"
    assert len(result.provenance["attach_attempts"]) == 1


@pytest.mark.asyncio
async def test_attachment_refused_retries_once_then_harvests() -> None:
    page = MagicMock()
    refused = ReattachOutcome(ok=False, error="attachment.refused")
    opened = ReattachOutcome(
        ok=True,
        page=page,
        pw=MagicMock(),
        registration_id="reg-r",
    )
    ensure = AsyncMock(side_effect=[refused, opened])
    harvest_page = AsyncMock(
        return_value=HarvestResponse(outcome="harvested", provenance={})
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest_open.ensure_cse_attached",
            ensure,
        ),
        patch(
            "cdp_ask.cse_session_harvest_open._teardown_attempt",
            AsyncMock(),
        ),
    ):
        result = await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            None,
            harvest_page,
        )
    assert ensure.await_count == 2
    assert result.outcome == "harvested"
    attempts = result.provenance["attach_attempts"]
    assert len(attempts) == 2
    assert attempts[0]["error"] == "attachment.refused"
    assert attempts[1]["reattach"] is True
    assert result.provenance.get("reattach") is True


@pytest.mark.asyncio
async def test_attachment_refused_twice_returns_not_attached_with_attempts() -> None:
    refused = ReattachOutcome(ok=False, error="attachment.refused")
    ensure = AsyncMock(return_value=refused)
    harvest_page = AsyncMock()
    with patch(
        "cdp_ask.cse_session_harvest_open.ensure_cse_attached",
        ensure,
    ):
        result = await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            None,
            harvest_page,
        )
    assert ensure.await_count == 2
    assert result.outcome == "not_attached"
    assert result.reason == "attachment.refused"
    assert len(result.provenance["attach_attempts"]) == 2


@pytest.mark.asyncio
async def test_found_tab_borrow_is_left_open_after_read() -> None:
    """A tab the harvest attach found is left open after the read."""
    page = MagicMock()
    opened = ReattachOutcome(
        ok=True,
        page=page,
        pw=MagicMock(),
        page_only_borrow=True,
        page_only_close_tab=False,
        cdp_url="http://127.0.0.1:9222",
        registration_id="reg-found",
    )

    async def _page(_page, _req, provenance=None, **_identity):
        return HarvestResponse(outcome="harvested", provenance=provenance)

    harvest_page = AsyncMock(side_effect=_page)
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
        result = await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            {"k": 1},
            harvest_page,
        )
    harvest_page.assert_awaited_once()
    teardown.assert_awaited_once_with(page, opened.pw, close_page=False)
    assert result.outcome == "harvested"
    assert result.provenance
    assert result.provenance.get("opened_on_demand") is True
    assert "cdp_url" not in result.provenance
    assert result.provenance.get("registration_id") == "reg-found"


@pytest.mark.asyncio
async def test_opened_tab_borrow_is_closed_after_read() -> None:
    """A tab the harvest attach opened is closed after the read."""
    page = MagicMock()
    pw = MagicMock()
    opened = ReattachOutcome(
        ok=True,
        page=page,
        pw=pw,
        lane_created=False,
        page_only_borrow=True,
        page_only_close_tab=True,
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
    teardown.assert_awaited_once_with(page, pw, close_page=True)


@pytest.mark.asyncio
async def test_minted_host_is_released_after_read() -> None:
    """A host minted for the read is closed and released after the teardown."""
    page = MagicMock()
    pw = MagicMock()
    opened = ReattachOutcome(
        ok=True,
        page=page,
        pw=pw,
        lane_created=True,
        registration_id="reg-new",
        page_only_borrow=True,
        page_only_close_tab=True,
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
        patch("claude_bundles.cdp_registry.deregister_lane", MagicMock()) as deregister,
    ):
        result = await harvest_by_opening_url(
            "https://claude.ai/cowork/cse_x",
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            None,
            harvest_page,
        )
    teardown.assert_awaited_once_with(page, pw, close_page=True)
    deregister.assert_called_once_with("reg-new")
    assert result.outcome == "incomplete_dom"
    assert result.reason == "loading"


@pytest.mark.asyncio
async def test_teardown_without_release_minted_keeps_minted_host() -> None:
    """Without release_minted, as execution resume calls it, no host is released."""
    page = MagicMock()
    pw = MagicMock()
    minted = ReattachOutcome(
        ok=True,
        page=page,
        pw=pw,
        lane_created=True,
        registration_id="reg-minted",
        page_only_borrow=True,
        page_only_close_tab=True,
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest_open._teardown_attempt",
            AsyncMock(),
        ) as teardown,
        patch("claude_bundles.cdp_registry.deregister_lane", MagicMock()) as deregister,
    ):
        await _teardown_opened(minted)
    teardown.assert_awaited_once_with(page, pw, close_page=True)
    deregister.assert_not_called()