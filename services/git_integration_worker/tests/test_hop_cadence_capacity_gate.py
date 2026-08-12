"""Hop cadence capacity gate — admit at free_slots >= 1 (arc 7119 hop gate)."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.cdp_escalation import (
    escalation_lane_refusal,
)
from services.git_integration_worker.cursor_auto.hop_cadence import capacity_blocks_hop


def test_escalation_lane_refusal_soft_blocks_unattended_at_free_slots_1() -> None:
    """Document the generic gate this hop path must not use."""
    refuse, label = escalation_lane_refusal(
        {"at_hard_limit": False, "at_soft_limit": True, "free_slots": 1},
        unattended=True,
    )
    assert refuse is True
    assert label == "soft"


def test_capacity_blocks_hop_admits_at_free_slots_1() -> None:
    blocked, label = capacity_blocks_hop(
        snapshot_reader=lambda: {
            "at_hard_limit": False,
            "at_soft_limit": True,
            "free_slots": 1,
        }
    )
    assert blocked is False
    assert label is None


def test_capacity_blocks_hop_refuses_at_hard_limit() -> None:
    blocked, label = capacity_blocks_hop(
        snapshot_reader=lambda: {
            "at_hard_limit": True,
            "at_soft_limit": True,
            "free_slots": 0,
        }
    )
    assert blocked is True
    assert label == "hard"


def test_capacity_blocks_hop_refuses_when_no_free_slots() -> None:
    blocked, label = capacity_blocks_hop(
        snapshot_reader=lambda: {
            "at_hard_limit": False,
            "at_soft_limit": True,
            "free_slots": 0,
        }
    )
    assert blocked is True
    assert label == "hard"
