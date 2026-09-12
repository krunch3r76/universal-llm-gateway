"""Tests for life scoreboard NOW projection and spawn render."""

from __future__ import annotations

import pytest

from bus_watch.life_scoreboard import NOW_RULE, project_now, render_spawn_scoreboard

pytestmark = pytest.mark.offline


def test_deadline_order_none_last() -> None:
    goals = [
        {
            "id": "todo:b",
            "status": "open",
            "deadline": None,
            "priority": 1,
            "obstacles": [],
        },
        {
            "id": "todo:a",
            "status": "open",
            "deadline": "2026-09-15",
            "priority": 5,
            "obstacles": [],
        },
    ]
    result = project_now(goals, as_of="2026-09-11T12:00:00Z")
    assert result is not None
    assert result["goal_id"] == "todo:a"


def test_priority_tiebreak() -> None:
    goals = [
        {
            "id": "todo:low",
            "status": "open",
            "deadline": "2026-09-20",
            "priority": 5,
            "obstacles": [],
        },
        {
            "id": "todo:high",
            "status": "open",
            "deadline": "2026-09-20",
            "priority": 1,
            "obstacles": [],
        },
    ]
    result = project_now(goals, as_of="2026-09-11T12:00:00Z")
    assert result is not None
    assert result["goal_id"] == "todo:high"


def test_standing_blocking_obstacle_skipped() -> None:
    goals = [
        {
            "id": "todo:blocked",
            "status": "open",
            "deadline": "2026-09-12",
            "priority": 1,
            "obstacles": [
                {"id": "a:99", "standing": True, "blocking": True, "text": "x"}
            ],
        },
        {
            "id": "todo:ok",
            "status": "open",
            "deadline": "2026-09-20",
            "priority": 2,
            "obstacles": [],
        },
    ]
    result = project_now(goals, as_of="2026-09-11T12:00:00Z")
    assert result is not None
    assert result["goal_id"] == "todo:ok"
    skipped = {s["goal_id"]: s["reason"] for s in result["skipped"]}
    assert skipped["todo:blocked"] == "standing_obstacle:a:99"


def test_waiting_on_world_never_now() -> None:
    goals = [
        {
            "id": "todo:wait",
            "status": "WAITING_ON_WORLD",
            "deadline": "2026-09-12",
            "priority": 1,
            "obstacles": [],
        },
    ]
    result = project_now(goals, as_of="2026-09-11T12:00:00Z")
    assert result is None


def test_render_fixed_headings_and_none_now() -> None:
    goals = [
        {
            "id": "todo:done",
            "status": "done",
            "deadline": None,
            "priority": None,
            "obstacles": [],
        },
    ]
    md = render_spawn_scoreboard(
        "probe",
        "matter:probe",
        goals,
        as_of="2026-09-11T12:00:00Z",
    )
    assert "## NOW" in md
    assert "## Goals" in md
    assert "## Steer log" in md
    assert "## Stops log" in md
    assert "todo:done" in md
    assert f"| _none_ | {NOW_RULE} | — | 2026-09-11T12:00:00Z |" in md
