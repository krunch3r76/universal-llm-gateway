"""Hermetic harvest no-side-effect and bounded extraction tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat_harvest.cse_persist import CSE_HARVEST_NO_LIMIT
from claude_bundles import cdp_registry

from cdp_ask.cse_session_harvest import execute_harvest
from cdp_ask.cse_session_models import CseSessionTurn, HarvestRequest, HarvestResponse
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_envelope import FollowupCandidate


@pytest.mark.asyncio
async def test_cse_harvest_chat_url_refuses_use_chat_session() -> None:
    store = ExecutionStore()
    result = await execute_harvest(
        HarvestRequest(chat_url="https://claude.ai/chat/thread-uuid"),
        store,
    )
    assert result.outcome == "refused"
    assert result.code == "use_chat_session"
    assert "chat_session" in (result.reason or "")


@pytest.mark.asyncio
async def test_harvest_high_limit_accepted() -> None:
    req = HarvestRequest(registration_id="reg-x", limit=999)
    assert req.limit == 999


@pytest.mark.asyncio
async def test_harvest_default_limit_forwards_unlimited_and_after_turn() -> None:
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
            "title": "Session - Claude",
            "loading": False,
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
        patch(
            "cdp_ask.cse_session_harvest_scrape.harvest_turns",
            harvest_mock,
        ),
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
    ):
        await execute_harvest(
            HarvestRequest(
                registration_id="reg-x",
                after_turn=after_turn,
                source="chat",
            ),
            store,
        )
    harvest_mock.assert_awaited_once()
    call_kwargs = harvest_mock.await_args.kwargs
    assert call_kwargs["limit"] == CSE_HARVEST_NO_LIMIT
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
async def test_dormant_seat_without_reattach_opens_and_scrapes() -> None:
    """Default harvest on a dormant seat opens the URL, scrapes, and tears down."""
    from claude_bundles.cdp_registry.models import DormantSeat

    store = ExecutionStore()
    chat = "https://claude.ai/cowork/cse_x"
    dormant = DormantSeat(
        registration_id="reg-dormant",
        chat_url=chat,
        profile_suffix="s",
        profile=Path("/tmp/p"),
        holder="h",
    )
    opened = HarvestResponse(
        outcome="harvested",
        turns=[
            CseSessionTurn(
                author="assistant",
                text="harvested reply body long enough.",
                source="cse-dom",
                ordinal=1,
            )
        ],
        provenance={
            "evidence_class": "observed",
            "opened_on_demand": True,
        },
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([], None, None)),
        ),
        patch(
            "cdp_ask.cse_session_harvest.cdp_registry.dormant_for_chat_url",
            lambda _u: dormant,
        ),
        patch(
            "cdp_ask.cse_session_harvest.cdp_registry.list_active",
            lambda: [],
        ),
        patch(
            "cdp_ask.cse_session_harvest.harvest_by_opening_url",
            AsyncMock(return_value=opened),
        ) as opener,
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
    ):
        result = await execute_harvest(HarvestRequest(chat_url=chat), store)
    opener.assert_awaited_once()
    assert result.outcome == "harvested"
    assert result.outcome != "dormant"
    assert result.chat_url == chat


@pytest.mark.asyncio
async def test_dormant_seat_reattach_opens_then_scrapes() -> None:
    """Opt-in reattach wakes a dormant seat and harvests without leaking cdp_url."""
    from claude_bundles.cdp_registry.models import DormantSeat

    store = ExecutionStore()
    chat = "https://claude.ai/cowork/cse_x"
    dormant = DormantSeat(
        registration_id="reg-dormant",
        chat_url=chat,
        profile_suffix="s",
        profile=Path("/tmp/p"),
        holder="h",
    )
    opened = HarvestResponse(
        outcome="harvested",
        turns=[
            CseSessionTurn(
                author="assistant",
                text="harvested reply body long enough.",
                source="cse-dom",
                ordinal=1,
            )
        ],
        provenance={
            "evidence_class": "observed",
            "opened_on_demand": True,
        },
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([], None, None)),
        ),
        patch(
            "cdp_ask.cse_session_harvest.cdp_registry.dormant_for_chat_url",
            lambda _u: dormant,
        ),
        patch(
            "cdp_ask.cse_session_harvest.cdp_registry.list_active",
            lambda: [],
        ),
        patch(
            "cdp_ask.cse_session_harvest.harvest_by_opening_url",
            AsyncMock(return_value=opened),
        ) as opener,
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
    ):
        result = await execute_harvest(
            HarvestRequest(chat_url=chat, reattach=True),
            store,
        )
    opener.assert_awaited_once()
    assert result.outcome == "harvested"
    assert len(result.turns) == 1
    assert result.provenance
    assert result.provenance.get("evidence_class") == "observed"
    assert "cdp_url" not in result.provenance


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
