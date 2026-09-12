"""Offline tests for alwaysapply_rules_census seat-effective plane."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.cursor.alwaysapply_rules_census import (
    _REMOVED_PLUGIN_RULES,
    RuleFile,
    seat_effective_tokens,
)
from services.git_integration_worker.cursor_seat_overlay import (
    LEAD_ONLY_PLUGIN_PATHS,
    PRUNE_ONLY_PLUGIN_PATHS,
    PRUNED_PLUGIN_PATHS,
)


@pytest.mark.offline
def test_overlay_lists_imported_not_copied() -> None:
    assert PRUNED_PLUGIN_PATHS
    assert PRUNE_ONLY_PLUGIN_PATHS
    assert LEAD_ONLY_PLUGIN_PATHS
    assert Path("rules") / "operator-posture_ulg.mdc" in PRUNED_PLUGIN_PATHS


@pytest.mark.offline
def test_seat_effective_excludes_pruned_plugin_rules() -> None:
    resident = [
        RuleFile("plugin", Path("/x/dispatch-kernel_ulg.mdc"), True, "required_gate", 100, 10),
        RuleFile(
            "plugin",
            Path("/x/operator-posture_ulg.mdc"),
            True,
            "required_gate",
            500,
            10,
        ),
        RuleFile("hub", Path("/x/core_ws.mdc"), True, "required_gate", 200, 10),
    ]
    seats = [
        RuleFile(
            "seats",
            Path("/y/interagent-posture_ulg.mdc"),
            True,
            "required_gate",
            300,
            10,
        ),
    ]
    assert seat_effective_tokens(resident, seats) == 100 + 200 + 300


@pytest.mark.offline
def test_removed_plugin_rules_tracks_overlay_lists() -> None:
    for relpath in PRUNED_PLUGIN_PATHS:
        if relpath.parts[0] == "rules":
            assert relpath in _REMOVED_PLUGIN_RULES
    for relpath in LEAD_ONLY_PLUGIN_PATHS:
        assert relpath in _REMOVED_PLUGIN_RULES

