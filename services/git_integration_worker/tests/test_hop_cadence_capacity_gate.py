"""Hop cadence capacity gate — advisory scalars only (global hard limit not enforced)."""

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
async def test_fire_hop_admits_even_at_reported_hard_limit(monkeypatch) -> None:
    from services.git_integration_worker.cursor_auto import hop_cadence as cadence_mod

    snap = {
        "at_hard_limit": True,
        "at_soft_limit": True,
        "free_slots": 0,
        "running_count": 3,
    }
    monkeypatch.setattr(
        cadence_mod,
        "run_continuity_hop_concurrent",
        AsyncMock(return_value={"ok": True, "execution_id": "exec-1"}),
    )
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
    assert outcome["decision"]["at_hard_limit"] is True
    assert outcome["decision"]["free_slots"] == 0
    assert outcome["decision"]["label"] is None


@pytest.mark.asyncio
async def test_fire_hop_admit_carries_capacity_in_decision(monkeypatch) -> None:
    from services.git_integration_worker.cursor_auto import hop_cadence as cadence_mod

    snap = {
        "at_hard_limit": False,
        "at_soft_limit": True,
        "free_slots": 1,
        "running_count": 2,
    }
    monkeypatch.setattr(
        cadence_mod,
        "run_continuity_hop_concurrent",
        AsyncMock(return_value={"ok": True, "execution_id": "exec-1"}),
    )
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


def test_escalation_lane_refusal_soft_is_advisory() -> None:
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
    assert refuse is False
    assert label is None


def test_capacity_blocks_hop_never_blocks() -> None:
    cap = capacity_blocks_hop(
        snapshot_reader=lambda: {
            "at_hard_limit": True,
            "at_soft_limit": True,
            "free_slots": 0,
            "running_count": 3,
        }
    )
    assert cap.blocked is False
    assert cap.label is None
    assert cap.free_slots == 0
    assert cap.at_hard_limit is True


@pytest.mark.parametrize(
    ("at_hard_limit", "free_slots"),
    [
        (True, 0),
        (True, 1),
        (False, 0),
        (False, 3),
    ],
)
def test_capacity_gate_never_blocks(
    at_hard_limit: bool,
    free_slots: int,
) -> None:
    cap = evaluate_capacity_gate(
        {
            "at_hard_limit": at_hard_limit,
            "at_soft_limit": free_slots <= 1,
            "free_slots": free_slots,
            "running_count": 3 - free_slots,
        }
    )
    assert cap.blocked is False
    assert cap.label is None
    assert cap.free_slots == free_slots
    assert cap.at_hard_limit is at_hard_limit
