"""Offline storm fuse simulation — forbid §5 ACs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from scripts.model_manager.ui.controller.charter_runner import storm_fuse
from scripts.model_manager.ui.controller.charter_runner.park_friction_mint import (
    mint_followon_with_fuse,
)
from scripts.model_manager.ui.controller.charter_runner.storm_fuse import (
    FuseIdentity,
    FUSE_THRESHOLD,
)


@pytest.fixture
def fuse_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(storm_fuse, "charter_runner_data_dir", lambda: tmp_path)
    state_path = tmp_path / storm_fuse._STATE_FILENAME
    return state_path


def _identity() -> FuseIdentity:
    return FuseIdentity(
        category="protocol",
        tip_gid="G9",
        mismatch_class="executor_mismatch",
    )


def _park_row(friction_id: int) -> dict:
    return {
        "id": friction_id,
        "claim": "[protocol] executor mismatch on tip G9",
        "attributes": {
            "conveyor_origin": True,
            "tip_gid": "G9",
            "mismatch_class": "executor_mismatch",
            "actionable": True,
        },
    }


@pytest.mark.offline
def test_three_identical_parks_trip_hold(fuse_state: Path) -> None:
    identity = _identity()
    for fid in (101, 102, 103):
        result = storm_fuse.record_park_friction(identity, fid)
    assert result.consecutive_count == FUSE_THRESHOLD
    assert result.tripped is True
    assert result.held is True
    assert storm_fuse.is_held() is True

    fourth = storm_fuse.record_park_friction(identity, 104)
    assert fourth.suppressed is True
    assert fourth.held_friction_id == 101
    assert storm_fuse.is_quarantined(101)
    assert storm_fuse.is_quarantined(102)
    assert storm_fuse.is_quarantined(103)


@pytest.mark.offline
def test_reset_clears_counter_and_quarantine(fuse_state: Path) -> None:
    identity = _identity()
    for fid in (201, 202, 203):
        storm_fuse.record_park_friction(identity, fid)
    assert storm_fuse.is_held()
    assert storm_fuse.is_quarantined(201)

    storm_fuse.reset_storm_fuse()
    assert not storm_fuse.is_held()
    assert not storm_fuse.is_quarantined(201)

    fresh = storm_fuse.record_park_friction(identity, 301)
    assert fresh.consecutive_count == 1
    assert fresh.tripped is False
    assert not storm_fuse.is_held()


@pytest.mark.offline
def test_mint_followon_refuses_quarantined(fuse_state: Path) -> None:
    identity = _identity()
    for fid in (401, 402, 403):
        storm_fuse.record_park_friction(identity, fid)
    row = _park_row(403)
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.park_friction_mint.mint_friction_followon"
    ) as mint:
        assert mint_followon_with_fuse(row, root_id="6110") is None
        mint.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.offline
async def test_file_park_friction_emits_trip_event(fuse_state: Path) -> None:
    identity = _identity()
    emitted: list[dict] = []

    async def capture(**payload: object) -> None:
        emitted.append(dict(payload))

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.park_friction_mint.file_charter_protocol_friction",
            side_effect=[601, 602, 603],
        ),
        patch(
            "scripts.model_manager.ui.controller.charter_runner.park_friction_mint.emit_storm_fuse_tripped",
            new=AsyncMock(side_effect=capture),
        ),
    ):
        from scripts.model_manager.ui.controller.charter_runner.park_friction_mint import (
            file_park_friction,
        )

        for _ in range(3):
            await file_park_friction(
                root_id="6110",
                window_index=25,
                note="executor mismatch",
                tip_gid=identity.tip_gid,
                mismatch_class=identity.mismatch_class,
            )

    assert len(emitted) == 1
    assert emitted[0]["consecutive_count"] == FUSE_THRESHOLD
    assert emitted[0]["held_friction_id"] == 601
    assert emitted[0]["mismatch_class"] == "executor_mismatch"
    assert storm_fuse.is_held()

    dedup_id = await file_park_friction(
        root_id="6110",
        window_index=25,
        note="executor mismatch",
        tip_gid=identity.tip_gid,
        mismatch_class=identity.mismatch_class,
    )
    assert dedup_id == 601


@pytest.mark.offline
def test_fuse_state_persisted(fuse_state: Path) -> None:
    identity = _identity()
    storm_fuse.record_park_friction(identity, 701)
    raw = json.loads(fuse_state.read_text(encoding="utf-8"))
    assert raw["consecutive_count"] == 1
    assert raw["consecutive_identity"] == identity.key()
