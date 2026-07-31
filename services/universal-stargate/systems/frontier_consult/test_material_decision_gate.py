"""Unit tests for post-harden material-decision panel split (AC9b)."""

from __future__ import annotations

from systems.frontier_consult.material_decision_gate import (
    material_decision_closeout_complete,
    material_decision_closeout_flags,
)


def test_material_decision_without_panel_incomplete() -> None:
    assert not material_decision_closeout_complete(
        material_decision_present=True,
        panel_artifact=None,
    )
    flags = material_decision_closeout_flags(
        material_decision_present=True,
        panel_artifact=None,
    )
    assert flags["material_decision_closeout_complete"] is False
    assert flags["material_decision_panel_required"] is True


def test_material_decision_with_valid_panel_complete() -> None:
    assert material_decision_closeout_complete(
        material_decision_present=True,
        panel_artifact={
            "consensus_disposition": "panel",
            "panel_families": ["openai", "anthropic"],
        },
    )
