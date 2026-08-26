"""Hermetic harvest no-side-effect and bounded extraction tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles import cdp_registry
from claude_bundles.cowork_output_download import HarvestBody
from pydantic import ValidationError

from cdp_ask.cse_session_harvest import HARVEST_HARD_CAP, execute_harvest, harvest_page
from cdp_ask.cse_session_models import HarvestRequest, HarvestResponse
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_envelope import FollowupCandidate


@pytest.mark.asyncio
async def test_harvest_hard_cap_schema_refusal() -> None:
    with pytest.raises(ValidationError):
        HarvestRequest(registration_id="reg-x", limit=999)


@pytest.mark.asyncio
async def test_harvest_hard_cap_clamp_forwards_limit_and_after_turn() -> None:
    candidate = FollowupCandidate(
        registration_id="reg-x",
        chat_url="https://claude.ai/cowork/cse_x",
        holder="h",
        purpose="ask",
        cdp_url="http://127.0.0.1:9222",
        provenance={"registration_id": "reg-x"},
    )
    lane = cdp_registry.Registration(
        registration_id="reg-x",
        port=9222,
        profile_suffix="x",
        profile=Path("/tmp/p"),
        cdp_url="http://127.0.0.1:9222",
        holder="h",
        purpose="ask",
    )
    harvest_mock = AsyncMock(
        return_value={
            "turns": [
                {
                    "author": "assistant",
                    "text": "Reply text long enough for harvest.",
                    "ordinal": 4,
                }
            ],
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "incomplete_dom": False,
            "truncated": False,
        }
    )
    pw = MagicMock()
    pw.stop = AsyncMock()
    store = ExecutionStore()
    after_turn = 3
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([candidate], None, None)),
        ),
        patch(
            "cdp_ask.cse_session_harvest.cdp_registry.list_active",
            lambda: [lane],
        ),
        patch(
            "cdp_ask.cse_session_harvest.connect_cdp",
            AsyncMock(return_value=(pw, MagicMock(), MagicMock(), MagicMock())),
        ),
        patch("cdp_ask.cse_session_harvest.harvest_turns", harvest_mock),
        patch("cdp_ask.cse_session_harvest.resolve_harvest_body", AsyncMock(return_value=None)),
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
    ):
        await execute_harvest(
            HarvestRequest(
                registration_id="reg-x",
                limit=HARVEST_HARD_CAP,
                after_turn=after_turn,
                source="chat",
            ),
            store,
        )
    harvest_mock.assert_awaited_once()
    call_kwargs = harvest_mock.await_args.kwargs
    assert call_kwargs["limit"] == HARVEST_HARD_CAP
    assert call_kwargs["limit"] <= HARVEST_HARD_CAP
    assert call_kwargs["after_turn"] == after_turn


@pytest.mark.asyncio
async def test_unattached_without_url_does_not_open() -> None:
    store = ExecutionStore()
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([], None, None)),
        ),
        patch("cdp_ask.cse_session_harvest.connect_cdp", AsyncMock()) as connect,
        patch(
            "cdp_ask.cse_session_harvest.harvest_by_opening_url",
            AsyncMock(),
        ) as opener,
    ):
        result = await execute_harvest(HarvestRequest(registration_id="missing"), store)
    connect.assert_not_called()
    opener.assert_not_awaited()
    assert result.outcome == "not_attached"
    assert result.reason == "no_target"


@pytest.mark.asyncio
async def test_unattached_with_url_opens_then_scrapes() -> None:
    store = ExecutionStore()
    opened = HarvestResponse(
        outcome="harvested",
        turns=[],
        provenance={"opened_on_demand": True},
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([], None, None)),
        ),
        patch(
            "cdp_ask.cse_session_harvest.cdp_registry.dormant_for_chat_url",
            lambda _u: None,
        ),
        patch(
            "cdp_ask.cse_session_harvest.harvest_by_opening_url",
            AsyncMock(return_value=opened),
        ) as opener,
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
    ):
        result = await execute_harvest(
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_x"),
            store,
        )
    opener.assert_awaited_once()
    assert result.outcome == "harvested"
    assert result.chat_url == "https://claude.ai/cowork/cse_x"
    assert result.provenance and result.provenance.get("opened_on_demand") is True


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
            {"turns": [{"author": "assistant", "text": _BANNER_ONLY, "ordinal": 2}]},
            {
                "turns": [
                    {"author": "assistant", "text": keep_body, "ordinal": 1},
                    {"author": "assistant", "text": _BANNER_ONLY, "ordinal": 2},
                ],
                "streaming": False,
                "stop": False,
                "tool_pause": False,
                "incomplete_dom": False,
                "truncated": False,
            },
        ]
    )
    page = MagicMock()
    with (
        patch("cdp_ask.cse_session_harvest.harvest_turns", harvest_mock),
        patch(
            "cdp_ask.cse_session_harvest.resolve_harvest_body",
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
    assert harvest_mock.await_count == 2
    assert result.outcome == "harvested"
    assert result.content_provenance == "cse-dom"
    texts = [turn.text for turn in result.turns]
    assert keep_body in texts

