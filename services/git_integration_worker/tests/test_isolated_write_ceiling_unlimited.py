"""Lane-B isolated write ceiling — unlimited default and hot-reload override file."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_worktree_registry import (
    _LANE_B_CAPACITY_OVERRIDE_BASENAME,
    _LANE_B_WRITE_CAPACITY_UNLIMITED,
    isolated_write_ceiling,
    lane_b_isolated_write_ceiling_unlimited,
    mintable_worktrees,
)


@pytest.fixture(autouse=True)
def gateway_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CURSOR_SDK_ISOLATED_WRITE_CEILING", raising=False)
    return tmp_path


def test_unlimited_when_env_and_override_missing() -> None:
    assert lane_b_isolated_write_ceiling_unlimited() is True
    assert isolated_write_ceiling() == _LANE_B_WRITE_CAPACITY_UNLIMITED


def test_override_file_unlimited_hot_reload(gateway_dir: Path) -> None:
    override = gateway_dir / _LANE_B_CAPACITY_OVERRIDE_BASENAME
    override.write_text("unlimited\n", encoding="utf-8")
    assert lane_b_isolated_write_ceiling_unlimited() is True
    assert isolated_write_ceiling() == _LANE_B_WRITE_CAPACITY_UNLIMITED


def test_override_file_numeric_beats_stale_env(
    gateway_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_SDK_ISOLATED_WRITE_CEILING", "99")
    override = gateway_dir / _LANE_B_CAPACITY_OVERRIDE_BASENAME
    override.write_text("4", encoding="utf-8")
    assert lane_b_isolated_write_ceiling_unlimited() is False
    assert isolated_write_ceiling() == 4


def test_env_numeric_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_SDK_ISOLATED_WRITE_CEILING", "8")
    assert lane_b_isolated_write_ceiling_unlimited() is False
    assert isolated_write_ceiling() == 8


def test_mintable_unlimited_skips_live_lane_subtraction(
    gateway_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (gateway_dir / _LANE_B_CAPACITY_OVERRIDE_BASENAME).write_text(
        "unset", encoding="utf-8"
    )
    assert mintable_worktrees() == _LANE_B_WRITE_CAPACITY_UNLIMITED
