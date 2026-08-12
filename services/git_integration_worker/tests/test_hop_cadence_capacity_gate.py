"""Hop cadence capacity gate — admit at free_slots >= 1 (arc 7119 hop gate)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.git_integration_worker.cursor_auto.cdp_escalation import (
    escalation_lane_refusal,
)
from services.git_integration_worker.cursor_auto.hop_cadence import (
    CapacityGateResult,
    capacity_blocks_hop,
    evaluate_capacity_gate,
    fire_hop_for_decision,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import HopDecision
from services.git_integration_worker.cursor_auto.queue import reset_queue_for_tests

pytestmark = pytest.mark.offline


def _fire_decision() -> HopDecision:
    return HopDecision(
        thread_id="6655",
        action="fire",
        reason="watch_seated_at",
        age_s=2000.0,
        threshold_s=1500.0,
        signal="watch_seated_at",
    )


@pytest.mark.asyncio
async def test_fire_hop_capacity_block_carries_decision_snapshot() -> None:
    snap = {
        "at_hard_limit": True,
        "at_soft_limit": True,
        "free_slots": 0,
        "running_count": 3,
    }

    outcome = await fire_hop_for_decision(
        _fire_decision(),
        queue=reset_queue_for_tests(durable=False),
        row={"from_agent": "web-anthropic"},
        snapshot_reader=lambda: snap,
    )

    assert outcome["reason"] == "capacity_blocked"
    assert outcome["decision"] == {
        "reason": "capacity_blocked",
        "free_slots": 0,
        "running_count": 3,
        "at_soft_limit": True,
        "at_hard_limit": True,
        "label": "hard",
    }


@pytest.mark.asyncio
async def test_fire_hop_admit_carries_capacity_in_decision(monkeypatch) -> None:
    from services.git_integration_worker.cursor_auto import hop_cadence as cadence_mod

    snap = {
        "at_hard_limit": False,
        "at_soft_limit": True,
        "free_slots": 1,
        "running_count": 2,
    }
    monkeypatch.setattr(cadence_mod, "run_continuity_hop_concurrent", AsyncMock(
        return_value={"ok": True, "execution_id": "exec-1"},
    ))
    monkeypatch.setattr(cadence_mod, "mark_hop_fired", lambda *a, **k: None)
    monkeypatch.setattr(
        cadence_mod,
        "refuse_cadence_hop_for_live_seat",
        lambda row, s: (False, None, {}),
    )
    monkeypatch.setattr(
        cadence_mod,
        "assess_standing_handoff",
        lambda tid: __import__(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch",
            fromlist=["StandingHandoffFreshness"],
        ).StandingHandoffFreshness("current", f"cortex://x/{tid}.md", None, 1.0),
    )

    outcome = await fire_hop_for_decision(
        _fire_decision(),
        queue=reset_queue_for_tests(durable=False),
        row={"from_agent": "web-anthropic", "registration_id": "reg-1"},
        snapshot_reader=lambda: snap,
    )

    assert outcome["ok"] is True
    assert outcome["decision"]["free_slots"] == 1
    assert outcome["decision"]["running_count"] == 2
    assert outcome["decision"]["at_soft_limit"] is True
    assert outcome["decision"]["at_hard_limit"] is False
    assert outcome["decision"]["label"] is None


def test_escalation_lane_refusal_soft_blocks_unattended_at_free_slots_1() -> None:
    """Document the generic gate this hop path must not use."""
    rows = [
        {"purpose": "ask", "status": "running"},
        {"purpose": "ask", "status": "running"},
    ]
    refuse, label = escalation_lane_refusal(
        {
            "rows": rows,
            "at_hard_limit": False,
            "at_soft_limit": True,
            "free_slots": 1,
        },
        unattended=True,
    )
    assert refuse is True
    assert label == "soft"


def test_capacity_blocks_hop_admits_at_free_slots_1() -> None:
    cap = capacity_blocks_hop(
        snapshot_reader=lambda: {
            "at_hard_limit": False,
            "at_soft_limit": True,
            "free_slots": 1,
            "running_count": 2,
        }
    )
    assert cap == CapacityGateResult(
        blocked=False,
        label=None,
        free_slots=1,
        running_count=2,
        at_soft_limit=True,
        at_hard_limit=False,
    )


def test_capacity_blocks_hop_refuses_at_hard_limit() -> None:
    cap = capacity_blocks_hop(
        snapshot_reader=lambda: {
            "at_hard_limit": True,
            "at_soft_limit": True,
            "free_slots": 0,
            "running_count": 3,
        }
    )
    assert cap.blocked is True
    assert cap.label == "hard"
    assert cap.as_decision_dict() == {
        "free_slots": 0,
        "running_count": 3,
        "at_soft_limit": True,
        "at_hard_limit": True,
        "label": "hard",
    }


def test_capacity_blocks_hop_refuses_when_no_free_slots() -> None:
    cap = capacity_blocks_hop(
        snapshot_reader=lambda: {
            "at_hard_limit": False,
            "at_soft_limit": True,
            "free_slots": 0,
            "running_count": 3,
        }
    )
    assert cap.blocked is True
    assert cap.label == "hard"


@pytest.mark.parametrize(
    ("at_hard_limit", "free_slots", "expected_blocked", "expected_label"),
    [
        (True, 0, True, "hard"),
        (True, 1, True, "hard"),
        (True, 2, True, "hard"),
        (True, 3, True, "hard"),
        (False, 0, True, "hard"),
        (False, 1, False, None),
        (False, 2, False, None),
        (False, 3, False, None),
    ],
)
def test_capacity_gate_verdict_enumeration(
    at_hard_limit: bool,
    free_slots: int,
    expected_blocked: bool,
    expected_label: str | None,
) -> None:
    """Every free_slots × at_hard_limit state gets an explicit admit/block verdict."""
    cap = evaluate_capacity_gate(
        {
            "at_hard_limit": at_hard_limit,
            "at_soft_limit": free_slots <= 1,
            "free_slots": free_slots,
            "running_count": 3 - free_slots,
        }
    )
    assert cap.blocked is expected_blocked
    assert cap.label is expected_label
    assert cap.free_slots == free_slots
    assert cap.at_hard_limit is at_hard_limit
    decision = cap.as_decision_dict()
    assert decision["label"] is expected_label
    assert decision["free_slots"] == free_slots
    assert decision["running_count"] == 3 - free_slots
