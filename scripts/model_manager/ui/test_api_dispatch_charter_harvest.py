"""Tests for sync_restart_charter_harvest outcome vocabulary."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_mcp_deferred_never_reports_ok_at_harvest_path() -> None:
    """AC-1: a deferred manage response must not present as success."""
    from scripts.model_manager.ui.api_dispatch import sync_restart_charter_harvest

    ctl = MagicMock()
    deferred = {
        "status": "deferred",
        "state": "busy",
        "reason": "cdp_ask_live",
    }
    with patch(
        "scripts.model_manager.ui.api_dispatch._mcp_deferred_sync_restart",
        new=AsyncMock(return_value=deferred),
    ):
        result = await sync_restart_charter_harvest(ctl, "mcp")

    assert result["status"] == "deferred"
    assert result["outcome"] == "declined"
    assert result.get("status") != "ok"


@pytest.mark.asyncio
async def test_mcp_ok_after_restart_reports_proven() -> None:
    from scripts.model_manager.ui.api_dispatch import sync_restart_charter_harvest

    ctl = MagicMock()
    with (
        patch(
            "scripts.model_manager.ui.api_dispatch._mcp_deferred_sync_restart",
            new=AsyncMock(return_value={"status": "ok", "message": "scheduled"}),
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch._wait_healthy",
            new=AsyncMock(return_value=1.5),
        ),
    ):
        result = await sync_restart_charter_harvest(ctl, "mcp")

    assert result["status"] == "ok"
    assert result["outcome"] == "proven"
    assert result["wait_healthy_s"] == 1.5
