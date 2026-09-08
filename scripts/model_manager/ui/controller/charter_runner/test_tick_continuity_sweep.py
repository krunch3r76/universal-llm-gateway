"""Charter-runner continuity sweep tick leg."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.model_manager.ui.controller.charter_runner import (
    tick_continuity_sweep as mod,
)


@pytest.mark.asyncio
async def test_sweep_on_tick_delegates_to_run_continuity_sweep() -> None:
    with patch(
        "agent_bus_store.continuity_sweep.run_continuity_sweep",
        return_value=[{"root": "10223", "trigger_thread": "10303", "turn": 5}],
    ) as sweep:
        results = await mod.sweep_role_roots_on_tick()

    assert results == [{"root": "10223", "trigger_thread": "10303", "turn": 5}]
    sweep.assert_called_once_with()


@pytest.mark.asyncio
async def test_sweep_failure_does_not_raise() -> None:
    with (
        patch(
            "agent_bus_store.continuity_sweep.run_continuity_sweep",
            side_effect=RuntimeError("boom"),
        ),
        patch.object(mod.logger, "exception", new=MagicMock()),
    ):
        results = await mod.sweep_role_roots_on_tick()

    assert results == []
