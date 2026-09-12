"""Hop only for autonomous follow-up (operator 2026-09-12 07:26 PT)."""

from __future__ import annotations

import pytest

from bus_watch.hop_qualify import hop_qualifies

pytestmark = pytest.mark.offline


def test_hold_merge_is_stay() -> None:
    q = hop_qualifies(
        row="HOLD_MERGE 10561 6eb762bf + 10567 bb1ca6bd; 3b parked"
    )
    assert q == {"ok": False, "reason": "hold_merge"}


def test_land_owed_is_stay() -> None:
    q = hop_qualifies(row="LAND OWED 10561 after AC-9")
    assert q["ok"] is False
    assert q["reason"] == "hold_merge"


def test_empty_now_is_stay() -> None:
    assert hop_qualifies(row="  ") == {"ok": False, "reason": "empty_now"}


def test_live_watcher_qualifies_even_on_hold_merge() -> None:
    q = hop_qualifies(
        row="HOLD_MERGE 10561",
        arm_labels=["10534-g6-amend"],
    )
    assert q == {"ok": True, "reason": "live_watcher"}


def test_operator_gate_is_stay() -> None:
    q = hop_qualifies(row="R15 3c wake parked OPERATOR_GATE")
    assert q == {"ok": False, "reason": "operator_gate"}


def test_dispatchable_now_qualifies() -> None:
    q = hop_qualifies(row="R16 GPT removal densify")
    assert q == {"ok": True, "reason": "dispatchable_now"}


def test_context_budget_stays_on_hold_merge() -> None:
    q = hop_qualifies(row="HOLD_MERGE 10561", context_budget=True)
    assert q["ok"] is False
    assert q["reason"] == "hold_merge"


def test_context_budget_hops_when_work_remains() -> None:
    q = hop_qualifies(row="G4 implement packet", context_budget=True)
    assert q == {"ok": True, "reason": "context_budget"}
