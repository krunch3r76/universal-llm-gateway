"""Lane ordering and closeout detection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from maestro_induct.lanes import (
    HOP_HARVEST_AVAILABLE,
    build_lane_record,
    is_closeout_turn,
    order_lanes_newest_first,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_lanes_newest_first_numeric() -> None:
    children = json.loads((FIXTURES / "lineage-12286.json").read_text())["children"]
    ordered = order_lanes_newest_first(children)
    assert [c["thread_id"] for c in ordered] == ["12359", "12291", "9999"]


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("12263-turn11-closeout.txt", True),
        ("15326-turn-18-closeout.json", True),
        ("12291-turn-12-consult.json", False),
    ],
)
def test_closeout_detector_shapes(fixture: str, expected: bool) -> None:
    path = FIXTURES / fixture
    if fixture.endswith(".json"):
        turn = json.loads(path.read_text())
    else:
        turn = {"subject": "", "body": path.read_text(encoding="utf-8"), "turn_number": 11}
    assert is_closeout_turn(turn) is expected


def test_lane_from_hop_view_12263_t11() -> None:
    if not HOP_HARVEST_AVAILABLE:
        pytest.skip("operator_hop_harvest not installed")
    from unittest.mock import patch

    import maestro_induct.lanes as lanes_mod

    body11 = (FIXTURES / "12263-turn11-closeout.txt").read_text(encoding="utf-8")
    live_subject = (
        "cursor-sdk CLOSEOUT 0e048a74c604-eb383ae1 contract=conductor caller=conductor-hop"
    )
    turn12 = {
        "turn_number": 12,
        "subject": "cdp FAILED — 53739643",
        "body": "# markdown",
    }
    turn11 = {"turn_number": 11, "subject": live_subject, "body": body11}
    turn10 = {
        "turn_number": 10,
        "subject": "cursor-sdk CLOSEOUT 0e048a74c604-eb383ae1 contract=conductor",
        "body": "",
    }
    lane = {"thread_id": "12263", "lane_role": "dispatch", "status": "active"}
    captured: dict = {}

    real_assemble = lanes_mod.assemble_operator_hop_view

    def _spy(*args, **kwargs):
        view = real_assemble(*args, **kwargs)
        captured["view"] = view
        return view

    with patch.object(lanes_mod, "assemble_operator_hop_view", side_effect=_spy):
        rec = lanes_mod.build_lane_record(
            lane=lane, tail_turns=[turn12, turn11, turn10], root="12286"
        )
    assert rec["source"] == "hop_harvest"
    assert rec["closeout_turn"] == 11
    assert rec["stop_tokens"] == ["DONE", "ROW_PINNED"]
    assert rec["next_admit"] is None
    assert rec["wait_kind"] == "none"
    view = captured["view"]
    assert rec["closeout_turn"] == view["conductor"]["closeout_turn"]
    assert rec["stop_tokens"] == view["conductor"]["stop_tokens"]
    assert rec["next_admit"] == view["conductor"]["next_admit"]
    assert rec["wait_kind"] == view["wait"]["kind"]
    assert len(json.dumps(rec, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) <= 400


def test_lane_from_hop_view_15326_t18_bare_json() -> None:
    turn = json.loads((FIXTURES / "15326-turn-18-closeout.json").read_text())
    lane = {"thread_id": "15326", "lane_role": "dispatch", "status": "active"}
    rec = build_lane_record(lane=lane, tail_turns=[turn], root="12286")
    assert rec["source"] == "hop_harvest"
    assert rec["closeout_turn"] == 18


def test_hop_harvest_absent_degrades_to_bus_tail(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "operator_hop_harvest", None)
    import importlib

    import maestro_induct.lanes as lanes_mod

    importlib.reload(lanes_mod)
    turn = {"turn_number": 1, "subject": "NOTE", "body": "x", "from": "cursor"}
    rec = lanes_mod.build_lane_record(
        lane={"thread_id": "1", "status": "active", "lane_role": None},
        tail_turns=[turn],
        root="12286",
    )
    assert rec["source"] == "bus_tail"
