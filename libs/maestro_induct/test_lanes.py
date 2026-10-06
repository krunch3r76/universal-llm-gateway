"""Lane ordering and closeout detection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from maestro_induct.lanes import (
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
