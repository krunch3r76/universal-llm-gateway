"""Tests for unified closeout tree-state projection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.closeout_tree_state import (
    CloseoutTreeState,
    compose_deployment_authorship,
    compute_closeout_tree_state,
    deployment_state_contradicts_checkpoint,
)

pytestmark = pytest.mark.offline


def test_deployment_state_contradicts_when_owed_without_commit() -> None:
    assert deployment_state_contradicts_checkpoint(
        checkpoint="deferred: authored paths not yet path-explicit committed",
        deployment_state=(
            "5 propagation-owed paths — see RESIDUE block; liveness: unknown"
        ),
    )


def test_deployment_state_contradicts_legacy_landed_not_live_marker() -> None:
    assert deployment_state_contradicts_checkpoint(
        checkpoint="deferred: authored paths not yet path-explicit committed",
        deployment_state="5 landed-not-live paths — see RESIDUE block",
    )


def test_deployment_state_not_contradictory_when_committed() -> None:
    assert not deployment_state_contradicts_checkpoint(
        checkpoint="committed abc1234 paths=3",
        deployment_state=(
            "2 propagation-owed paths — see RESIDUE block; liveness: unknown"
        ),
    )


def test_deployment_state_not_contradictory_when_authored_not_committed() -> None:
    assert not deployment_state_contradicts_checkpoint(
        checkpoint="deferred: authored paths not yet path-explicit committed",
        deployment_state="authored-not-committed — 2 paths await path-explicit commit",
    )


def test_compute_closeout_tree_state_uncommitted_never_claims_landed() -> None:
    wrapper = (
        '{"schema_version":1,"status":"complete",'
        '"files_modified":["libs/charter_runner_store/db.py"],'
        '"propagation_residue":["sync_restart: git_integration_worker — manage(...)"]}'
    )
    usable_baseline = {
        "codes": {"libs/ambient.py": " M"},
        "hashes": {"libs/ambient.py": "a" * 64},
        "admit_head": "abc123",
        "outside_repo": [],
    }
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "CursorDispatchLedger.instance",
    ) as ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.authored_paths_for_dispatch",
        return_value=("libs/charter_runner_store/db.py", "libs/foo.py"),
    ):
        ledger_cls.return_value.read_wt_baseline.return_value = usable_baseline
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-uncommitted",
            wrapper_text=wrapper,
        )
    assert isinstance(state, CloseoutTreeState)
    assert state.deployment_state is not None
    assert "authored-not-committed@local-master" in state.deployment_state
    assert "landed-not-live" not in state.deployment_state
    assert "propagation-owed" not in state.deployment_state
    assert state.plane_line.startswith("plane:")
    assert not deployment_state_contradicts_checkpoint(
        checkpoint=state.checkpoint,
        deployment_state=state.deployment_state,
    )


def test_compute_closeout_tree_state_committed_allows_obligation() -> None:
    wrapper = (
        '{"schema_version":1,"status":"complete",'
        '"files_modified":["services/git_integration_worker/x.py"],'
        '"propagation_residue":["sync_restart: git_integration_worker — manage(...)"]}'
    )
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.compute_lane_a_checkpoint_value",
        return_value="committed abc1234 paths=1",
    ):
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-committed",
            wrapper_text=wrapper,
        )
    assert state.deployment_state is not None
    assert "propagation-owed@local-master" in state.deployment_state
    assert "liveness: unknown" in state.deployment_state
    assert "landed-not-live" not in state.deployment_state
    assert "not yet live" not in state.deployment_state
    assert state.checkpoint.startswith("committed@local-master ")
    assert state.plane_line.startswith("plane:")
    assert not deployment_state_contradicts_checkpoint(
        checkpoint=state.checkpoint,
        deployment_state=state.deployment_state,
    )


def test_compute_closeout_tree_state_nothing_authored_has_no_deployment_state() -> None:
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-empty",
            wrapper_text='{"schema_version":1,"status":"complete"}',
        )
    assert state.deployment_state is None
    assert state.checkpoint == "nothing_authored@local-master"
    assert state.plane_line.startswith("plane:")


def test_compute_closeout_tree_state_threads_wrapper_for_authored_cortex() -> None:
    """Row 19 — tree_state passes wrapper_text + cortex_root into compute."""
    wrapper = (
        '{"schema_version":1,"files_offgit_produced":'
        '["cortex://notes/system/threads/x.md"]}'
    )
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="authored_cortex: cortex://notes/system/threads/x.md "
        + ("e" * 64),
    ) as compute, patch(
        "implement_admission.closeout_helpers.cortex_files_root",
        return_value=Path("/tmp/cortex-root"),
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "CursorDispatchLedger.instance",
    ) as ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.authored_paths_for_dispatch",
        return_value=(),
    ):
        ledger_cls.return_value.read_wt_baseline.return_value = None
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-cortex",
            wrapper_text=wrapper,
        )
    assert state.checkpoint.startswith("authored_cortex@local-master:")
    # Rank 1: missing baseline under non-nothing_authored → refuse, not mute.
    assert state.deployment_state is not None
    assert "attribution-unavailable@local-master" in state.deployment_state
    assert "authored-not-committed" not in state.deployment_state
    assert state.plane_line.startswith("plane:")
    kwargs = compute.call_args.kwargs
    assert kwargs["wrapper_text"] == wrapper
    assert kwargs["cortex_root"] == Path("/tmp/cortex-root")


def test_compose_empty_authored_registration_gap_emits_unavailability() -> None:
    """Case (b): producing seat cannot register → greppable unavailability, not omit."""
    claim = compose_deployment_authorship(
        baseline={
            "codes": {},
            "hashes": {},
            "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
            "outside_repo": [],
        },
        authored=(),
        ledger_registration_available=False,
    )
    assert claim is not None
    assert "ledger-registration-unavailable" in claim
    assert "attribution-unavailable" not in claim
    assert "authored-not-committed" not in claim


def test_compose_empty_authored_registering_seat_omits() -> None:
    """Case (a): ledger consulted, seat registers, nothing ledger-proven → omit."""
    claim = compose_deployment_authorship(
        baseline={
            "codes": {},
            "hashes": {},
            "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
            "outside_repo": [],
        },
        authored=(),
        ledger_registration_available=True,
    )
    assert claim is None
    assert "authored-not-committed" not in (claim or "")
    assert "attribution-unavailable" not in (claim or "")
    assert "ledger-registration-unavailable" not in (claim or "")


def test_compose_rank2_empty_codes_ambient_omits_when_unproven() -> None:
    """Must-not: empty admit codes + ambient dirt without ledger proof → omit."""
    claim = compose_deployment_authorship(
        baseline={
            "codes": {},
            "hashes": {},
            "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
            "outside_repo": [],
        },
        authored=(),
        ledger_registration_available=True,
    )
    assert claim is None
    assert "authored-not-committed" not in (claim or "")
    assert "attribution-unavailable" not in (claim or "")


def test_compose_rank2_empty_codes_ledger_edit_fires() -> None:
    """Rank 2 restore: clean-admit codes={} + ledger-proven lane edit → fire."""
    claim = compose_deployment_authorship(
        baseline={
            "codes": {},
            "hashes": {},
            "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
            "outside_repo": [],
        },
        authored=("services/git_integration_worker/cursor_auto/nested_outcome.py",),
    )
    assert claim == (
        "authored-not-committed — 1 path await path-explicit commit"
    )


def test_compose_rank2_populated_baseline_delta_still_fires() -> None:
    """Must-fire: ledger-proven delta under populated admit baseline → fire."""
    ambient = (
        ".gitignore",
        "libs/cdp_ask/runner.py.orig",
        "scripts/watch-giw-wedge-stackdump.py",
        "scripts/watch-giw-wedge-tmux.sh",
    )
    claim = compose_deployment_authorship(
        baseline={
            "codes": {p: "??" for p in ambient},
            "hashes": {p: "d" * 64 for p in ambient},
            "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
            "outside_repo": [],
        },
        authored=("services/git_integration_worker/cursor_auto/nested_outcome.py",),
    )
    assert claim == (
        "authored-not-committed — 1 path await path-explicit commit"
    )


def test_compose_rank1_missing_baseline_refuses() -> None:
    claim = compose_deployment_authorship(baseline=None, authored=())
    assert claim == "attribution-unavailable — admit baseline missing"


def test_rank2_both_directions_via_compute_closeout_tree_state() -> None:
    """Both AC arms green in one compute path (must-not omit + must-fire)."""
    empty_baseline = {
        "codes": {},
        "hashes": {},
        "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
        "outside_repo": [],
    }
    usable_baseline = {
        "codes": {".gitignore": " M"},
        "hashes": {".gitignore": "d" * 64},
        "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
        "outside_repo": [],
    }
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "CursorDispatchLedger.instance",
    ) as ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.authored_paths_for_dispatch",
        side_effect=[(), ("services/git_integration_worker/x.py",)],
    ):
        ledger_cls.return_value.read_wt_baseline.side_effect = [
            empty_baseline,
            usable_baseline,
        ]
        omit = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-ambient",
        )
        fire = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-lane-edit",
        )
    assert omit.deployment_state is not None
    assert "ledger-registration-unavailable" in omit.deployment_state
    assert "@local-master" in omit.deployment_state
    assert fire.deployment_state is not None
    assert "authored-not-committed@local-master" in fire.deployment_state
    assert "1 path" in fire.deployment_state
