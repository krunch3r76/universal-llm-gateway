"""Offline tests for single-mission now_row multiplex guard."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bus_watch.multiplex_guard import assess_now_row_multiplex

pytestmark = pytest.mark.offline


def test_idempotent_rebind_same_todo() -> None:
    policy = {"single_mission_todo": "todo:primary-arc", "now_row": "todo:primary-arc"}
    out = assess_now_row_multiplex("19455", policy, "todo:primary-arc")
    assert out["ok"] is True
    assert out["action"] == "idempotent"


def test_friction_gate_swap_allowed() -> None:
    policy = {"single_mission_todo": "todo:primary-arc", "now_row": "todo:primary-arc"}
    out = assess_now_row_multiplex("19455", policy, "friction:39814:night-abc")
    assert out["ok"] is True
    assert out["action"] == "friction_gate_swap"


def test_allowed_work_key_passes() -> None:
    policy = {
        "single_mission_todo": "todo:primary-arc",
        "now_row": "todo:primary-arc",
        "allowed_work_keys": ["todo:child-step"],
    }
    out = assess_now_row_multiplex("19455", policy, "todo:child-step")
    assert out["ok"] is True


def test_second_distinct_todo_refused_on_16416() -> None:
    policy = {
        "now_row": "todo:primary-arc",
    }
    with patch("bus_watch.multiplex_guard.emit_multiplex_refused") as refused:
        out = assess_now_row_multiplex("16416", policy, "todo:unrelated-arc")
    assert out["ok"] is False
    assert out["error"] == "multiplex_refused"
    refused.assert_called_once()
