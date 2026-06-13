"""Unit tests for material-decision panel split gate."""

from __future__ import annotations

from systems.frontier_consult.material_decision_gate import (
    material_decision_closeout_complete,
    material_decision_closeout_flags,
)


def test_no_material_decision_always_complete() -> None:
    assert material_decision_closeout_complete(
        material_decision_present=False,
        panel_artifact=None,
    )


def test_material_decision_without_panel_incomplete() -> None:
    assert not material_decision_closeout_complete(
        material_decision_present=True,
        panel_artifact=None,
    )


def test_steering_reviewer_alone_insufficient() -> None:
    assert not material_decision_closeout_complete(
        material_decision_present=True,
        panel_artifact={"consensus_disposition": "steelman-only"},
    )


def test_post_harden_panel_satisfies() -> None:
    assert material_decision_closeout_complete(
        material_decision_present=True,
        panel_artifact={
            "consensus_disposition": "panel",
            "panel_families": ["openai", "anthropic"],
        },
    )


def test_flags_surface_incomplete() -> None:
    flags = material_decision_closeout_flags(
        material_decision_present=True,
        panel_artifact=None,
    )
    assert flags["material_decision_closeout_complete"] is False
