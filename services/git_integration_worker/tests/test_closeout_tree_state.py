"""Tests for unified closeout tree-state projection."""

from __future__ import annotations

import inspect
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

_VACANCY = (
    "ledger-registration-unavailable — cursor-sdk paths not in seat write ledger"
)


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


def test_compose_omit_records_authorship_outcome_at_decision() -> None:
    """AC — non-firing omit is tallied; silence must not leave the meter dark."""
    captured: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        captured.append(kwargs)

    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "emit_authorship_outcome",
        side_effect=_capture,
    ):
        claim = compose_deployment_authorship(
            baseline={
                "codes": {},
                "hashes": {},
                "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
                "outside_repo": [],
            },
            authored=(),
            ledger_registration_available=True,
            dispatch_id="d-omit-tally",
        )
    assert claim is None
    assert len(captured) == 1
    assert captured[0]["outcome"] == "omit"
    assert captured[0]["dispatch_id"] == "d-omit-tally"
    assert captured[0]["baseline_present"] is True
    assert captured[0]["ledger_registration_available"] is True
    assert captured[0]["authored_count"] == 0


def test_compose_vacancy_records_authorship_outcome_at_decision() -> None:
    """Vacancy fire is tallied with the same instrument as omit (paired numerator)."""
    captured: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        captured.append(kwargs)

    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "emit_authorship_outcome",
        side_effect=_capture,
    ):
        claim = compose_deployment_authorship(
            baseline={
                "codes": {},
                "hashes": {},
                "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
                "outside_repo": [],
            },
            authored=(),
            ledger_registration_available=False,
            dispatch_id="d-vacancy-tally",
        )
    assert claim is not None
    assert _VACANCY in claim
    assert len(captured) == 1
    assert captured[0]["outcome"] == "vacancy"
    assert captured[0]["dispatch_id"] == "d-vacancy-tally"
    assert captured[0]["baseline_present"] is True
    assert captured[0]["ledger_registration_available"] is False


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


def test_nr1_compose_vacancy_when_unavailable_red() -> None:
    """NR1 — explicit False at compose ceiling must emit greppable vacancy."""
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
    assert _VACANCY in claim


def test_nr2_wiring_derives_from_has_paths_for_arc() -> None:
    """NR2 — compute_closeout_tree_state passes has_paths_for_arc(dispatch_id)."""
    usable_baseline = {
        "codes": {},
        "hashes": {},
        "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
        "outside_repo": [],
    }
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "CursorDispatchLedger.instance",
    ) as ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "authored_paths_for_dispatch",
        return_value=(),
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "SeatWriteLedger.instance",
    ) as seat_ledger_cls:
        ledger_cls.return_value.read_wt_baseline.return_value = usable_baseline
        seat_ledger = seat_ledger_cls.return_value
        seat_ledger.has_paths_for_arc.return_value = True
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-nr2",
        )
        seat_ledger.has_paths_for_arc.assert_called_once_with(arc_id="d-nr2")
    assert state.deployment_state is None


def test_nr3_failed_population_vacancy_when_zero_arc_rows() -> None:
    """NR3 / AC-2 — failed-but-terminal: baseline + no rows + empty authored → vacancy."""
    usable_baseline = {
        "codes": {".gitignore": " M"},
        "hashes": {".gitignore": "d" * 64},
        "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
        "outside_repo": [],
    }
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "CursorDispatchLedger.instance",
    ) as ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "authored_paths_for_dispatch",
        return_value=(),
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "SeatWriteLedger.instance",
    ) as seat_ledger_cls:
        ledger_cls.return_value.read_wt_baseline.return_value = usable_baseline
        seat_ledger_cls.return_value.has_paths_for_arc.return_value = False
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-failed-terminal",
        )
    assert state.deployment_state is not None
    assert _VACANCY in state.deployment_state
    assert "@local-master" in state.deployment_state


def test_nr4_compute_body_has_no_literal_registration_flag() -> None:
    """NR4 — production compose site must not hardcode True/False."""
    from services.git_integration_worker.cursor_auto import closeout_tree_state

    source = inspect.getsource(closeout_tree_state.compute_closeout_tree_state)
    assert "ledger_registration_available=True" not in source
    assert "ledger_registration_available=False" not in source


def _capture_emit() -> tuple[list[dict[str, object]], object]:
    """Return (bucket, side_effect) for patching emit_authorship_outcome."""
    captured: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        captured.append(kwargs)

    return captured, _capture


def test_arm_checkpoint_committed_records_authorship_outcome() -> None:
    """Arm 1 — checkpoint claims committed → outcome=checkpoint_committed; render unchanged."""
    captured, _capture = _capture_emit()
    wrapper = (
        '{"schema_version":1,"status":"complete",'
        '"files_modified":["services/git_integration_worker/x.py"],'
        '"propagation_residue":["sync_restart: git_integration_worker — manage(...)"]}'
    )
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="committed abc1234 paths=1",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "emit_authorship_outcome",
        side_effect=_capture,
    ):
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-arm-committed",
            wrapper_text=wrapper,
        )
    assert state.deployment_state is not None
    assert "propagation-owed@local-master" in state.deployment_state
    assert len(captured) == 1
    assert captured[0]["outcome"] == "checkpoint_committed"
    assert captured[0]["dispatch_id"] == "d-arm-committed"
    assert captured[0]["baseline_present"] is False
    assert captured[0]["authored_count"] == 0


def test_arm_nothing_authored_records_authorship_outcome() -> None:
    """Arm 2 — nothing_authored → outcome=nothing_authored; deployment_state stays None."""
    captured, _capture = _capture_emit()
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "emit_authorship_outcome",
        side_effect=_capture,
    ):
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-arm-nothing",
            wrapper_text='{"schema_version":1,"status":"complete"}',
        )
    assert state.deployment_state is None
    assert state.checkpoint == "nothing_authored@local-master"
    assert len(captured) == 1
    assert captured[0]["outcome"] == "nothing_authored"
    assert captured[0]["dispatch_id"] == "d-arm-nothing"
    assert captured[0]["baseline_present"] is False


def test_arm_compose_vacancy_records_via_compute() -> None:
    """Arm 3 — compose→vacancy still tallied when reached through compute."""
    captured, _capture = _capture_emit()
    usable_baseline = {
        "codes": {".gitignore": " M"},
        "hashes": {".gitignore": "d" * 64},
        "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
        "outside_repo": [],
    }
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "CursorDispatchLedger.instance",
    ) as ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "authored_paths_for_dispatch",
        return_value=(),
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "SeatWriteLedger.instance",
    ) as seat_ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "emit_authorship_outcome",
        side_effect=_capture,
    ):
        ledger_cls.return_value.read_wt_baseline.return_value = usable_baseline
        seat_ledger_cls.return_value.has_paths_for_arc.return_value = False
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-arm-vacancy",
        )
    assert state.deployment_state is not None
    assert _VACANCY in state.deployment_state
    assert len(captured) == 1
    assert captured[0]["outcome"] == "vacancy"
    assert captured[0]["dispatch_id"] == "d-arm-vacancy"
    assert captured[0]["ledger_registration_available"] is False


def test_arm_compose_omit_records_via_compute() -> None:
    """Arm 4 — compose→omit tallied through compute (registering seat, empty authored)."""
    captured, _capture = _capture_emit()
    empty_baseline = {
        "codes": {},
        "hashes": {},
        "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
        "outside_repo": [],
    }
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "CursorDispatchLedger.instance",
    ) as ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "authored_paths_for_dispatch",
        return_value=(),
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "SeatWriteLedger.instance",
    ) as seat_ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "emit_authorship_outcome",
        side_effect=_capture,
    ):
        ledger_cls.return_value.read_wt_baseline.return_value = empty_baseline
        seat_ledger_cls.return_value.has_paths_for_arc.return_value = True
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-arm-omit",
        )
    assert state.deployment_state is None
    assert len(captured) == 1
    assert captured[0]["outcome"] == "omit"
    assert captured[0]["dispatch_id"] == "d-arm-omit"
    assert captured[0]["ledger_registration_available"] is True


def test_arm_compose_authored_not_committed_records_via_compute() -> None:
    """Arm 5 — compose→authored_not_committed tallied through compute."""
    captured, _capture = _capture_emit()
    usable_baseline = {
        "codes": {".gitignore": " M"},
        "hashes": {".gitignore": "d" * 64},
        "admit_head": "6cf34833ea361a0b694e8ff169e476c06f329b95",
        "outside_repo": [],
    }
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "CursorDispatchLedger.instance",
    ) as ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "authored_paths_for_dispatch",
        return_value=("services/git_integration_worker/x.py",),
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "SeatWriteLedger.instance",
    ) as seat_ledger_cls, patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "emit_authorship_outcome",
        side_effect=_capture,
    ):
        ledger_cls.return_value.read_wt_baseline.return_value = usable_baseline
        seat_ledger_cls.return_value.has_paths_for_arc.return_value = True
        state = compute_closeout_tree_state(
            source_repo=Path("/tmp/unused"),
            dispatch_id="d-arm-anc",
        )
    assert state.deployment_state is not None
    assert "authored-not-committed@local-master" in state.deployment_state
    assert len(captured) == 1
    assert captured[0]["outcome"] == "authored_not_committed"
    assert captured[0]["dispatch_id"] == "d-arm-anc"
    assert captured[0]["authored_count"] == 1
