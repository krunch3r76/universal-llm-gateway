"""Shared fixtures for controller offline charter tests."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner import (
    seed_phase1 as seed_phase1_mod,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import SeedConfirm


@pytest.fixture(autouse=True)
def _hermetic_charter_runner_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    data = tmp_path_factory.mktemp("charter-runner-data")
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data))


@pytest.fixture(autouse=True)
def _auto_seed_unknown_test_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed unknown roots as attended. Does not read CHARTER_ADMISSION_MODE."""
    real_ensure = seed_phase1_mod.ensure_root_ledger_seed

    def _ensure(root_id: str, *, default: SeedConfirm | None = None) -> bool:
        if default is None and root_id and root_id not in seed_phase1_mod._PHASE1_BY_ID:
            default = SeedConfirm(
                root_id=root_id,
                pickup_gid="G2",
                pickup_lane="judgment",
                attendance="attended",
                scoreboard_uri=(
                    f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md"
                ),
            )
        return real_ensure(root_id, default=default)

    monkeypatch.setattr(seed_phase1_mod, "ensure_root_ledger_seed", _ensure)


@pytest.fixture(autouse=True)
def _reset_unattended_stale_reminders() -> None:
    from scripts.model_manager.ui.controller.charter_runner import unattended_stale

    unattended_stale._reminded.clear()
    yield
    unattended_stale._reminded.clear()
