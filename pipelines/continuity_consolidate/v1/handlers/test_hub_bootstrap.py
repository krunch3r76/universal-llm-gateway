"""Hub bootstrap for multi-lane consolidate-continuity ingest."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pipelines.continuity_consolidate.v1.handlers import _cortex as cortex

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_ensure_hub_returns_existing_without_create():
    client = AsyncMock()
    hub = {"id": "document:9582-continuity", "name": "9582 house"}
    with patch.object(cortex, "resolve_hub", AsyncMock(return_value=hub)):
        got, bootstrap = await cortex.ensure_hub(client, "9582", {"slug": "money"})
    assert got == hub
    assert bootstrap is None
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_hub_mints_when_missing():
    client = AsyncMock()
    created = {"id": "document:9740-continuity", "name": "9740 perps trader house"}
    resolve_calls = 0

    async def _resolve(_client, root):
        nonlocal resolve_calls
        resolve_calls += 1
        return None if resolve_calls == 1 else created

    with patch.object(cortex, "resolve_hub", side_effect=_resolve):
        with patch.object(
            cortex,
            "dispatch",
            AsyncMock(return_value={"entity": created}),
        ) as dispatch:
            got, bootstrap = await cortex.ensure_hub(
                client,
                "9740",
                {
                    "slug": "perps-trader-lighter",
                    "summary": "No-fire perps trader prototype.",
                },
            )
    assert got == created
    assert bootstrap == "bootstrapped"
    dispatch.assert_awaited_once()
    args = dispatch.await_args.args
    assert args[1] == "entity_create"
    assert args[2]["id"] == "document:9740-continuity"
    assert "No-fire perps trader" in args[2]["description"]


def test_default_hub_description_uses_summary():
    text = cortex.default_hub_description(
        "10327", {"slug": "perps-trader-continuity", "summary": "Monitor lane."}
    )
    assert "Mission: Monitor lane." in text
    assert "agent-bus:10327" in text
