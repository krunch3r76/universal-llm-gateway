"""Hermetic harvest no-side-effect and bounded extraction tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles import cdp_registry
from pydantic import ValidationError

from cdp_ask.cse_session_harvest import HARVEST_HARD_CAP, execute_harvest
from cdp_ask.cse_session_models import HarvestRequest
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
async def test_dormant_without_relaunch(monkeypatch) -> None:
    seat = cdp_registry.DormantSeat(
        registration_id="reg-d",
        chat_url="https://claude.ai/cowork/cse_d",
        profile_suffix="d",
        profile=Path("/tmp/p"),
        holder="h",
        purpose="ask",
        dormant_at=1.0,
    )
    monkeypatch.setattr(cdp_registry, "dormant_for_chat_url", lambda _u: seat)
    store = ExecutionStore()
    with patch(
        "cdp_ask.cse_session_harvest.discover_candidates",
        AsyncMock(return_value=([], None, None)),
    ):
        result = await execute_harvest(
            HarvestRequest(chat_url="https://claude.ai/cowork/cse_d"),
            store,
        )
    assert result.outcome == "dormant"


@pytest.mark.asyncio
async def test_harvest_never_calls_connect_cdp_when_unattached() -> None:
    store = ExecutionStore()
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([], None, None)),
        ),
        patch("cdp_ask.cse_session_harvest.connect_cdp", AsyncMock()) as connect,
        patch("cdp_ask.cse_session_harvest.cdp_registry.dormant_for_chat_url", lambda _u: None),
    ):
        await execute_harvest(HarvestRequest(chat_url="https://claude.ai/cowork/x"), store)
    connect.assert_not_called()
