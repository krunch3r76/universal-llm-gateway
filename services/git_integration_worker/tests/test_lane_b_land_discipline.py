"""Unit tests for Lane-B land incompleteness + branch-list marker strip."""

from __future__ import annotations

from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.cursor_sdk_land_discipline import (
    LANE_B_UNLANDED_DEVIATION,
    apply_lane_b_land_incompleteness,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    normalize_git_branch_list_name,
)


def test_unlanded_lane_b_downgrades_complete() -> None:
    status, deviations = apply_lane_b_land_incompleteness(
        CloseoutStatus.COMPLETE,
        lane="B",
        landed=False,
        commits_ahead=1,
        deviations=None,
    )
    assert status == CloseoutStatus.PARTIAL
    assert deviations == [LANE_B_UNLANDED_DEVIATION]


def test_landed_or_empty_lane_b_unchanged() -> None:
    status, deviations = apply_lane_b_land_incompleteness(
        CloseoutStatus.COMPLETE,
        lane="B",
        landed=True,
        commits_ahead=1,
        deviations=["keep"],
    )
    assert status == CloseoutStatus.COMPLETE
    assert deviations == ["keep"]

    status, deviations = apply_lane_b_land_incompleteness(
        CloseoutStatus.COMPLETE,
        lane="B",
        landed=False,
        commits_ahead=0,
        deviations=None,
    )
    assert status == CloseoutStatus.COMPLETE
    assert deviations is None


def test_lane_a_unaffected() -> None:
    status, deviations = apply_lane_b_land_incompleteness(
        CloseoutStatus.COMPLETE,
        lane="A",
        landed=False,
        commits_ahead=2,
        deviations=None,
    )
    assert status == CloseoutStatus.COMPLETE
    assert deviations is None


def test_unknown_landed_null_does_not_downgrade() -> None:
    """Preserve-no-data: landed=None must not fire land:lane_b_unlanded."""
    status, deviations = apply_lane_b_land_incompleteness(
        CloseoutStatus.COMPLETE,
        lane="B",
        landed=None,
        commits_ahead=2,
        deviations=None,
    )
    assert status == CloseoutStatus.COMPLETE
    assert deviations is None


def test_normalize_git_branch_list_name_strips_worktree_plus() -> None:
    assert (
        normalize_git_branch_list_name("+ cursor-sdk/auto-c3c2defa7bac")
        == "cursor-sdk/auto-c3c2defa7bac"
    )
    assert (
        normalize_git_branch_list_name("* cursor-sdk/foo") == "cursor-sdk/foo"
    )
    assert normalize_git_branch_list_name("  master") == "master"
