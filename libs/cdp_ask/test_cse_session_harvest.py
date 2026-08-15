"""Hermetic harvest no-side-effect and bounded extraction tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cdp_ask.cse_session_harvest import HARVEST_HARD_CAP, execute_harvest
from cdp_ask.cse_session_models import HarvestRequest
from cdp_ask.execution_store import ExecutionStore


@pytest.mark.asyncio
async def test_harvest_hard_cap() -> None:
    req = HarvestRequest(registration_id="reg-x", limit=50)
    assert req.limit == HARVEST_HARD_CAP


@pytest.mark.asyncio
async def test_dormant_without_relaunch(monkeypatch) -> None:
    from claude_bundles import cdp_registry

    from pathlib import Path

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
