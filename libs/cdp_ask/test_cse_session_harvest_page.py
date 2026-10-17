"""Hermetic harvest_page extraction tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles.cowork_output_download import HarvestBody

from cdp_ask.cse_session_harvest import harvest_page
from cdp_ask.cse_session_models import HarvestRequest

_BANNER_ONLY = (
    "Claude responded: API Error: 529 Overloaded.\n\n"
    "API Error: 529 Overloaded. This is a server-side issue, usually temporary "
    "— try again in a moment. If it persists, check https://status.claude.com."
)


@pytest.mark.asyncio
async def test_auto_error_banner_preview_falls_through_to_full_scrape() -> None:
    """a:30411: error-banner last turn does not prove the CSE is empty."""
    keep_body = "KEEP 4 — document:life-coding-playbook verdict body."
    harvest_mock = AsyncMock(
        side_effect=[
            {
                "turns": [{"author": "assistant", "text": _BANNER_ONLY, "ordinal": 2}],
                "title": "Session - Claude",
            },
            {
                "turns": [{"author": "assistant", "text": _BANNER_ONLY, "ordinal": 2}],
                "title": "Session - Claude",
            },
            {
                "turns": [
                    {"author": "assistant", "text": keep_body, "ordinal": 1},
                    {"author": "assistant", "text": _BANNER_ONLY, "ordinal": 2},
                ],
                "streaming": False,
                "stop": False,
                "tool_pause": False,
                "title": "Session - Claude",
            },
        ]
    )
    page = MagicMock()
    with (
        patch("cdp_ask.cse_session_harvest_scrape.harvest_turns", harvest_mock),
        patch(
            "cdp_ask.cse_session_harvest_scrape.resolve_harvest_body",
            AsyncMock(
                return_value=HarvestBody(content=_BANNER_ONLY, provenance="chat")
            ),
        ),
    ):
        result = await harvest_page(
            page,
            HarvestRequest(source="auto", limit=10),
            provenance=None,
        )
    assert harvest_mock.await_count >= 2
    assert result.outcome == "harvested"
    assert result.content_provenance == "cse-dom"
    texts = [turn.text for turn in result.turns]
    assert keep_body in texts


@pytest.mark.asyncio
async def test_open_on_demand_loading_then_overlay_title_harvests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open-on-demand loading shell waits, then harvests when overlay/title settles."""
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_scrape.OPEN_ON_DEMAND_INCOMPLETE_WAIT_S",
        5.0,
    )
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_scrape.OPEN_ON_DEMAND_INCOMPLETE_POLL_S",
        0.0,
    )
    harvest_mock = AsyncMock(
        side_effect=[
            {
                "turns": [],
                "streaming": False,
                "title": "New chat - Claude",
                "spinner": False,
                "aria_busy": False,
            },
            {
                "turns": [{"author": "assistant", "text": "late reply", "ordinal": 1}],
                "streaming": False,
                "stop": False,
                "tool_pause": False,
                "title": "Playbook - Claude",
            },
        ]
    )
    page = MagicMock()
    with (
        patch("cdp_ask.cse_session_harvest_scrape.harvest_turns", harvest_mock),
        patch("cdp_ask.cse_session_harvest_scrape.asyncio.sleep", AsyncMock()),
        patch(
            "cdp_ask.cse_session_harvest_scrape.resolve_harvest_body",
            AsyncMock(return_value=None),
        ),
    ):
        result = await harvest_page(
            page,
            HarvestRequest(source="chat", limit=10),
            provenance={"opened_on_demand": True},
        )
    assert harvest_mock.await_count == 2
    assert result.outcome == "harvested"
    assert result.turns[0].text == "late reply"


@pytest.mark.asyncio
async def test_overlay_body_refused_while_loading() -> None:
    harvest_mock = AsyncMock(
        return_value={
            "turns": [],
            "streaming": False,
            "title": "New task - Claude",
            "spinner": True,
            "aria_busy": False,
        }
    )
    page = MagicMock()
    with (
        patch("cdp_ask.cse_session_harvest_scrape.harvest_turns", harvest_mock),
        patch(
            "cdp_ask.cse_session_harvest_scrape.resolve_harvest_body",
            AsyncMock(
                return_value=HarvestBody(content="premature body", provenance="chat")
            ),
        ),
        patch("cdp_ask.cse_session_harvest_scrape.asyncio.sleep", AsyncMock()),
        patch(
            "cdp_ask.cse_session_harvest_scrape.OPEN_ON_DEMAND_INCOMPLETE_WAIT_S",
            0.0,
        ),
    ):
        result = await harvest_page(
            page,
            HarvestRequest(source="auto", limit=10),
            provenance={"opened_on_demand": True},
        )
    assert result.outcome == "incomplete_dom"
    assert result.reason == "loading"


@pytest.mark.asyncio
async def test_attached_settled_empty_returns_no_reply_yet() -> None:
    harvest_mock = AsyncMock(
        return_value={
            "turns": [],
            "streaming": False,
            "title": "Named session - Claude",
            "spinner": False,
            "aria_busy": False,
        }
    )
    page = MagicMock()
    with patch("cdp_ask.cse_session_harvest_scrape.harvest_turns", harvest_mock):
        result = await harvest_page(
            page,
            HarvestRequest(source="chat", limit=10),
            provenance={"opened_on_demand": False},
        )
    assert harvest_mock.await_count == 1
    assert result.outcome == "no_reply_yet"
    assert result.reason == "settled_empty"


@pytest.mark.asyncio
async def test_attached_chrome_only_last_assistant_returns_streaming() -> None:
    from chat_harvest.test_chrome import SPECIMEN_346_BODY

    harvest_mock = AsyncMock(
        return_value={
            "turns": [
                {
                    "author": "assistant",
                    "text": SPECIMEN_346_BODY,
                    "ordinal": 2,
                }
            ],
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "title": "Session - Claude",
            "loading": False,
        }
    )
    page = MagicMock()
    with patch("cdp_ask.cse_session_harvest_scrape.harvest_turns", harvest_mock):
        result = await harvest_page(
            page,
            HarvestRequest(source="chat", limit=10),
            provenance={"opened_on_demand": False},
        )
    assert result.outcome == "streaming"
    assert result.tool_pause is True
    assert result.streaming is True


@pytest.mark.asyncio
async def test_attached_loading_waits_then_incomplete_dom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_scrape.OPEN_ON_DEMAND_INCOMPLETE_WAIT_S",
        0.0,
    )
    harvest_mock = AsyncMock(
        return_value={
            "turns": [],
            "streaming": False,
            "title": "New chat - Claude",
            "spinner": True,
            "aria_busy": False,
        }
    )
    page = MagicMock()
    with (
        patch("cdp_ask.cse_session_harvest_scrape.harvest_turns", harvest_mock),
        patch("cdp_ask.cse_session_harvest_scrape.asyncio.sleep", AsyncMock()),
    ):
        result = await harvest_page(
            page,
            HarvestRequest(source="chat", limit=10),
            provenance={"opened_on_demand": False},
        )
    assert harvest_mock.await_count >= 1
    assert result.outcome == "incomplete_dom"
    assert result.reason == "loading"
