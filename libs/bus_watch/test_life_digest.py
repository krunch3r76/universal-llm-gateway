"""Tests for life block composer (F4/F5, Stops (b), C3, Amendment A1)."""

from __future__ import annotations

import json

import pytest

from bus_watch.consent_projection import is_gated
from bus_watch.life_digest import (
    LIFE_BLOCK_CAP,
    MAX_STOP_ROWS,
    build_life_block,
    project_life_block,
)
from bus_watch.life_stops import LIFE_KNOB_DEFAULTS

pytestmark = pytest.mark.offline

AS_OF = "2026-09-12T06:00:00Z"


def test_empty_inputs() -> None:
    block = build_life_block(goals=[], consents=[], facts={}, policy={}, as_of=AS_OF)
    assert block["now"] is None
    assert block["stops"] == []
    assert block["gates"]["gates"] == []
    assert set(block["knobs"]) == set(LIFE_KNOB_DEFAULTS)
    for knob in block["knobs"].values():
        assert knob["source"] == "default"
    assert block["source"] == {"goals": 0, "consents": 0}


def test_now_deadline_rule() -> None:
    goals = [
        {
            "id": "todo:early",
            "status": "open",
            "deadline": "2026-09-15",
            "priority": 2,
            "obstacles": [],
        },
        {
            "id": "todo:late",
            "status": "open",
            "deadline": "2026-09-20",
            "priority": 1,
            "obstacles": [],
        },
        {
            "id": "todo:blocking",
            "status": "open",
            "deadline": "2026-09-25",
            "priority": 1,
            "obstacles": [{"id": "a:1", "blocking": True, "standing": False}],
        },
    ]
    block = build_life_block(goals=goals, consents=[], facts={}, policy={}, as_of=AS_OF)
    assert block["now"] is not None
    assert block["now"]["goal_id"] == "todo:early"


def test_standing_without_blocking_eligible() -> None:
    goals = [
        {
            "id": "todo:standing",
            "status": "open",
            "deadline": "2026-09-15",
            "priority": 1,
            "obstacles": [{"id": "a:2", "standing": True}],
        },
    ]
    block = build_life_block(goals=goals, consents=[], facts={}, policy={}, as_of=AS_OF)
    assert block["now"] is not None
    assert block["now"]["goal_id"] == "todo:standing"


def test_consent_gate_flows_through() -> None:
    consents = [
        {
            "id": "a:gate1",
            "class": "gate",
            "gate_class": "outbound_correspondence",
            "scope": "landlord",
        }
    ]
    block = build_life_block(
        goals=[], consents=consents, facts={}, policy={}, as_of=AS_OF
    )
    assert block["gates"]["gates"]
    assert is_gated("outbound_correspondence", "landlord", block["gates"]) is True


def test_caller_facts_and_gate_override() -> None:
    cap = LIFE_KNOB_DEFAULTS["spend_cap_dispatches_per_day"]
    caller_gates = {"gates": [], "lifts": [{"class": "money", "scope": "bank"}]}
    block = build_life_block(
        goals=[],
        consents=[],
        facts={"dispatches_today": cap + 1, "gates": caller_gates},
        policy={},
        as_of=AS_OF,
    )
    assert any(r["stop"] == "SPEND_CAP" for r in block["stops"])
    assert block["gates"] != caller_gates


def test_knobs_from_policy() -> None:
    block = build_life_block(
        goals=[],
        consents=[],
        facts={},
        policy={"deadline_near_days": 7},
        as_of=AS_OF,
    )
    assert block["knobs"]["deadline_near_days"]["value"] == 7
    assert block["knobs"]["deadline_near_days"]["source"] == "policy"


def _fat_block(*, prose: str = "SENTINEL-PROSE") -> dict:
    stops = [
        {
            "stop": f"STOP_{i}",
            "row": f"todo:{i}",
            "trigger": f"trigger-{i}",
            "action": "pause",
            "page": True,
            "carries": {"detail": f"x{i}"},
            "as_of": AS_OF,
        }
        for i in range(40)
    ]
    gates = [
        {
            "class": f"gate_class_{i}",
            "scope": f"scope_{i}",
            "expiry": "2026-12-01T00:00:00Z",
        }
        for i in range(8)
    ]
    lifts = [
        {
            "class": f"lift_{i}",
            "scope": f"lift_scope_{i}",
            "expiry": "2026-12-01T00:00:00Z",
        }
        for i in range(4)
    ]
    knobs = life_knobs_from_defaults()
    return {
        "as_of": AS_OF,
        "now": {
            "goal_id": "todo:now",
            "rule": "first-open-goal-by-deadline-priority-nonstanding",
            "source": ["todo:now"],
            "as_of": AS_OF,
            "skipped": [],
        },
        "gates": {"gates": gates, "lifts": lifts, "last_steer": None},
        "stops": stops,
        "knobs": knobs,
        "source": {"goals": 1, "consents": 0},
        "_prose": prose,
    }


def life_knobs_from_defaults() -> dict:
    from bus_watch.life_stops import life_knobs

    return life_knobs({}, as_of=AS_OF)


def test_projection_under_cap() -> None:
    block = _fat_block()
    proj = project_life_block(block)
    encoded = json.dumps(proj, separators=(",", ":"), ensure_ascii=False, default=str)
    assert len(encoded.encode("utf-8")) <= LIFE_BLOCK_CAP
    assert len(proj["stops"]) <= MAX_STOP_ROWS
    assert proj["now"] is not None
    assert ("truncated" in proj) == (
        len(block["stops"]) > MAX_STOP_ROWS or len(block["knobs"]) > 0
    )


def test_cap_enforcement() -> None:
    block = _fat_block()
    with pytest.raises(ValueError, match="64"):
        project_life_block(block, cap_bytes=64)
    proj = project_life_block(block)
    a = json.dumps(project_life_block(block), separators=(",", ":"), sort_keys=True)
    b = json.dumps(proj, separators=(",", ":"), sort_keys=True)
    assert a == b


def test_forbidden_prose_absent() -> None:
    block = _fat_block(prose="SENTINEL-PROSE")
    block["now"] = {
        "goal_id": "todo:g1",
        "rule": "first-open-goal-by-deadline-priority-nonstanding",
        "as_of": AS_OF,
        "source": ["todo:g1"],
        "skipped": [{"goal_id": "todo:g2", "reason": "SENTINEL-PROSE in note"}],
    }
    goals_block = build_life_block(
        goals=[
            {
                "id": "todo:g1",
                "status": "open",
                "deadline": "2026-09-15",
                "priority": 1,
                "obstacles": [],
                "title": "SENTINEL-PROSE",
                "note": "SENTINEL-PROSE body",
            }
        ],
        consents=[],
        facts={},
        policy={},
        as_of=AS_OF,
    )
    proj = project_life_block(goals_block)
    serialized = json.dumps(
        proj, separators=(",", ":"), ensure_ascii=False, default=str
    )
    assert "SENTINEL-PROSE" not in serialized
