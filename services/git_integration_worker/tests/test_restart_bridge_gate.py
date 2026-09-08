"""Leg F.2 restart deferral when operator bridges are live (AC-S1-F3)."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.restart_drain import RestartDrainGate


@pytest.mark.asyncio
async def test_force_sync_restart_deferred_with_live_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S1-F3: force restart defers while mock live bridge is present."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_restart_bridge_gate.live_bridge_blocks_restart",
        lambda *, force: force,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_restart_bridge_gate.defer_restart_for_live_bridges",
        lambda **_: True,
    )

    gate = RestartDrainGate(probes={})
    outcome = await gate.evaluate("git_integration_worker", force=True)

    assert outcome is not None
    assert outcome.state == "draining"
    assert "live operator bridges" in outcome.reason
