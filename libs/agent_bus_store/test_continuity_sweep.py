"""Offline tests for charter periodic continuity sweep."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_bus_store import continuity_sweep as sweep
from agent_bus_store.db import create_thread_with_turn, init_db
from agent_bus_store.db.lane_associations import associate_lane
from agent_bus_store.db.turns import insert_turn

pytestmark = pytest.mark.offline

_CHECKPOINT_BODY = (
    "## Derived (projected at post — do not hand-edit)\n- noise\n\n"
    "## Residue (authored — cap ~800 chars)\n"
    "Mission: test house.\n"
)


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_CONTINUITY_CONSOLIDATE", "0")
    monkeypatch.setenv("AGENT_BUS_CONTINUITY_SWEEP", "1")
    monkeypatch.setenv("CONTINUITY_SWEEP_DRY_RUN", "0")
    init_db()


def _root_house(slug: str) -> str:
    row, *_ = create_thread_with_turn(
        slug=f"{slug}-house",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="CHECKPOINT — open",
        body=_CHECKPOINT_BODY,
        tags=["role:root"],
    )
    return row["id"]


def _lane(parent: str, slug: str) -> str:
    row, *_ = create_thread_with_turn(
        slug=f"{slug}-lane",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="dispatch",
        body="go",
    )
    associate_lane(thread_id=row["id"], parent_thread_id=parent, lane_role="sub_mission")
    return row["id"]


def _closeout(thread_id: str, *, n: int = 1) -> int:
    for _ in range(n):
        _, _, turn_number = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="cursor",
            subject="CLOSEOUT — done",
            body="fold me",
        )
    return turn_number


class TestParseWatermark:
    def test_parses_newest_pipeline_row(self) -> None:
        rows = [
            {
                "id": 1,
                "seeded_by": "continuity-consolidate",
                "claim": "WATERMARK: consolidated_through=10303#10 at 2026-01-01",
            },
            {
                "id": 2,
                "seeded_by": "other",
                "claim": "WATERMARK: consolidated_through=9999#99",
            },
            {
                "id": 3,
                "seeded_by": "continuity-consolidate",
                "claim": "WATERMARK: consolidated_through=10303#12 at 2026-01-02",
            },
        ]
        parsed = sweep.parse_watermark(rows)
        assert parsed == {
            "assertion_id": 3,
            "thread": "10303",
            "turn": 12,
            "claim": rows[2]["claim"],
        }


class TestFindStaleCloseout:
    def test_finds_lane_closeout_after_watermark(self, bus_db) -> None:
        root = _root_house("sweep-a")
        lane = _lane(root, "sweep-a")
        turn = _closeout(lane)
        watermark = {"thread": lane, "turn": turn - 1}
        assert sweep.find_stale_closeout(root, watermark) == (lane, turn)

    def test_none_when_watermark_covers_closeout(self, bus_db) -> None:
        root = _root_house("sweep-b")
        lane = _lane(root, "sweep-b")
        turn = _closeout(lane)
        watermark = {"thread": lane, "turn": turn}
        assert sweep.find_stale_closeout(root, watermark) is None

    def test_root_closeout_when_no_watermark(self, bus_db) -> None:
        root = _root_house("sweep-c")
        turn = _closeout(root)
        assert sweep.find_stale_closeout(root, None) == (root, turn)

    def test_ignores_checkpoint_only(self, bus_db) -> None:
        root = _root_house("sweep-d")
        insert_turn(
            thread=root,
            from_agent="cursor",
            to_agent="web-anthropic",
            subject="CHECKPOINT — still open",
            body="no fold",
        )
        assert sweep.find_stale_closeout(root, None) is None


class TestSweepRoot:
    def test_dry_run_logs_without_enqueue(self, bus_db, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CONTINUITY_SWEEP_DRY_RUN", "1")
        root = _root_house("dry")
        lane = _lane(root, "dry")
        turn = _closeout(lane)
        watermark = {"thread": lane, "turn": turn - 1}
        with (
            patch.object(sweep, "fetch_hub_watermark", return_value=watermark),
            patch.object(sweep, "_schedule_debounced") as schedule,
        ):
            schedule.reset_mock()
            record = sweep.sweep_root(root)
        assert record == {
            "root": root,
            "trigger_thread": lane,
            "turn": turn,
            "watermark": watermark,
            "dry_run": True,
        }
        schedule.assert_not_called()

    def test_live_calls_schedule_debounced(self, bus_db, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CONTINUITY_SWEEP_DRY_RUN", "0")
        root = _root_house("live")
        lane = _lane(root, "live")
        turn = _closeout(lane)
        watermark = {"thread": lane, "turn": turn - 1}
        with (
            patch.object(sweep, "fetch_hub_watermark", return_value=watermark),
            patch.object(sweep, "_schedule_debounced") as schedule,
        ):
            record = sweep.sweep_root(root)
        assert record is not None
        schedule.assert_called_once_with(root, lane, turn)


class TestRunContinuitySweep:
    def test_kill_switch(self, bus_db, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AGENT_BUS_CONTINUITY_SWEEP", "0")
        with patch.object(sweep, "sweep_root") as sweep_root:
            assert sweep.run_continuity_sweep() == []
        sweep_root.assert_not_called()

    def test_consolidate_kill_switch(self, bus_db, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AGENT_BUS_CONTINUITY_CONSOLIDATE", "0")
        with patch.object(sweep, "sweep_root") as sweep_root:
            assert sweep.run_continuity_sweep() == []
        sweep_root.assert_not_called()

    def test_rate_limit(self, bus_db, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AGENT_BUS_CONTINUITY_CONSOLIDATE", "1")
        monkeypatch.setenv("CONTINUITY_SWEEP_MAX_ROOTS", "2")
        with (
            patch.object(sweep, "list_role_root_ids", return_value=["1001", "1002"]),
            patch.object(sweep, "sweep_root", return_value=None) as sweep_root,
        ):
            sweep.run_continuity_sweep()
        assert sweep_root.call_count == 2

    def test_run_returns_action_records(self, bus_db, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_BUS_CONTINUITY_CONSOLIDATE", "1")
        record = {"root": "10223", "trigger_thread": "10303", "turn": 9, "dry_run": False}
        with (
            patch.object(sweep, "list_role_root_ids", return_value=["10223"]),
            patch.object(sweep, "sweep_root", return_value=record),
        ):
            assert sweep.run_continuity_sweep() == [record]
