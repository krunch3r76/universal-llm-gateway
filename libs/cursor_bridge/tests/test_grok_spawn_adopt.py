"""Adopt-by-name respects pre-create roster snapshot."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.grok_spawn_adopt import adopt_agent_by_name

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_adopt_rejects_preexisting_same_name_when_snapshotted() -> None:
    listed = {
        "ok": True,
        "agents": [{"id": "old-1", "name": "bot"}],
    }
    with patch(
        "cursor_bridge.grok_agent_ops.list_grok_agents_async",
        new_callable=AsyncMock,
        return_value=listed,
    ):
        out = await adopt_agent_by_name(
            "9", "bot", pre_create_ids=frozenset({"old-1"})
        )
    assert out["ok"] is False
    assert out["code"] == "adopt_preexisting_only"
    assert out["needs_operator"] is True


@pytest.mark.asyncio
async def test_adopt_picks_new_row_not_in_snapshot() -> None:
    listed = {
        "ok": True,
        "agents": [
            {"id": "old-1", "name": "bot"},
            {"id": "new-1", "name": "bot"},
        ],
    }
    with patch(
        "cursor_bridge.grok_agent_ops.list_grok_agents_async",
        new_callable=AsyncMock,
        return_value=listed,
    ):
        out = await adopt_agent_by_name(
            "9", "bot", pre_create_ids=frozenset({"old-1"})
        )
    assert out["ok"] is True
    assert out["agent_id"] == "new-1"
    assert out["adopted"] is True


@pytest.mark.asyncio
async def test_adopt_without_snapshot_refuses_same_name_rows() -> None:
    listed = {
        "ok": True,
        "agents": [{"id": "any-1", "name": "bot"}],
    }
    with patch(
        "cursor_bridge.grok_agent_ops.list_grok_agents_async",
        new_callable=AsyncMock,
        return_value=listed,
    ):
        out = await adopt_agent_by_name("9", "bot", pre_create_ids=None)
    assert out["ok"] is False
    assert out["code"] == "adopt_snapshot_missing"
    assert out["needs_operator"] is True
