"""Tests for unified closeout tree-state projection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.closeout_tree_state import (
    CloseoutTreeState,
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
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state.authored_paths_for_dispatch",
        return_value=("libs/charter_runner_store/db.py", "libs/foo.py"),
    ):
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-uncommitted",
            wrapper_text=wrapper,
        )
    assert isinstance(state, CloseoutTreeState)
    assert state.deployment_state is not None
    assert "authored-not-committed" in state.deployment_state
    assert "landed-not-live" not in state.deployment_state
    assert "propagation-owed" not in state.deployment_state
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
    assert "propagation-owed" in state.deployment_state
    assert "liveness: unknown" in state.deployment_state
    assert "landed-not-live" not in state.deployment_state
    assert "not yet live" not in state.deployment_state
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
    ):
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-cortex",
            wrapper_text=wrapper,
        )
    assert state.checkpoint.startswith("authored_cortex:")
    assert state.deployment_state is None
    kwargs = compute.call_args.kwargs
    assert kwargs["wrapper_text"] == wrapper
    assert kwargs["cortex_root"] == Path("/tmp/cortex-root")
