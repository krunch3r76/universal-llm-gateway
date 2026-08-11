"""Hermetic tests for nest-parent dual-meter closeout honesty (6655 G5).

Single tip-window enumeration stamps authored ``commits_ahead`` and twin
``commits_ahead_unfiltered``; S10b/S10c asymmetric failure shapes are
unreachable-by-construction.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_home import dispatch_git_identity
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_deliverables_expected import (
    admit_landed_true,
)
from services.git_integration_worker.cursor_sdk_git_head import (
    enumerate_tip_window_commits,
    observed_lane_git_refs,
    partition_tip_window_meters,
    tip_window_meter_counts,
)

pytestmark = pytest.mark.offline


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "master", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test"],
        check=True,
        capture_output=True,
    )


def _commit(repo: Path, rel: str, *, dispatch_id: str | None = None) -> str:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", rel], check=True, capture_output=True)
    env = dict(os.environ)
    cmd = ["git", "-C", str(repo), "commit", "-m", "c"]
    if dispatch_id is not None:
        name, email = dispatch_git_identity(dispatch_id)
        cmd.extend([f"--author={name} <{email}>"])
        env.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
            }
        )
    subprocess.run(cmd, check=True, capture_output=True, env=env)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _outcome() -> SdkRunOutcome:
    return SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )


def test_nest_parent_authored_excludes_child_tip_commits(tmp_path: Path) -> None:
    """AC2: child tip commits during park inflate unfiltered only."""
    parent_id = "parent-nest-meter"
    child_id = "child-nest-meter"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    _commit(tmp_path, "parent.py", dispatch_id=parent_id)
    _commit(tmp_path, "child_a.py", dispatch_id=child_id)
    _commit(tmp_path, "child_b.py", dispatch_id=child_id)
    closeout = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=parent_id,
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-nest",
        work_item_ref=None,
        baseline={"admit_head": admit},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    authored = payload["commits_ahead"]
    unfiltered = payload["commits_ahead_unfiltered"]
    assert unfiltered - authored >= 2
    lane_refs = observed_lane_git_refs(
        tmp_path,
        dispatch_id=parent_id,
        admit_head=admit,
        closeout_head=closeout,
    )
    assert authored == len(lane_refs)
    assert authored < unfiltered


def test_no_nest_baseline_authored_equals_unfiltered(tmp_path: Path) -> None:
    """AC2 baseline: sole-dispatch tip window → authored == unfiltered."""
    dispatch_id = "solo-nest-meter"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    _commit(tmp_path, "solo.py", dispatch_id=dispatch_id)
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-solo",
        work_item_ref=None,
        baseline={"admit_head": admit},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["commits_ahead"] == payload["commits_ahead_unfiltered"]
    assert payload["commits_ahead"] >= 1


def test_missing_admit_head_omits_both_meter_keys_and_landed_null(
    tmp_path: Path,
) -> None:
    """AC3 / L3 / L9: missing admit_head → both keys absent; landed null."""
    _init_git_repo(tmp_path)
    _commit(tmp_path, "seed.py")
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="no-admit-meter",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-no-admit",
        work_item_ref=None,
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert "commits_ahead" not in payload
    assert "commits_ahead_unfiltered" not in payload
    assert payload.get("landed") is None


def test_empty_range_both_meters_present_zero(tmp_path: Path) -> None:
    """AC3 / S2: real admit + empty range → both meters present 0."""
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="empty-range-meter",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-empty",
        work_item_ref=None,
        baseline={"admit_head": admit},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["commits_ahead"] == 0
    assert payload["commits_ahead_unfiltered"] == 0


def test_single_enumerator_failure_co_collapses_both_meters_to_zero(
    tmp_path: Path,
) -> None:
    """S10a: one subprocess failure → both meters present 0 (co-collapse)."""
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    _commit(tmp_path, "after_admit.py")
    closeout = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with patch(
        "services.git_integration_worker.cursor_sdk_git_head.subprocess.run",
        side_effect=OSError("simulated git failure"),
    ):
        counts = tip_window_meter_counts(
            tmp_path,
            dispatch_id="fail-enum",
            admit_head=admit,
            closeout_head=closeout,
        )
    assert counts == (0, 0)


def test_partition_matches_observed_lane_git_refs_predicate(tmp_path: Path) -> None:
    """AC1: authored partition uses same membership as observed_lane_git_refs."""
    parent_id = "pred-parent"
    child_id = "pred-child"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    _commit(tmp_path, "p.py", dispatch_id=parent_id)
    _commit(tmp_path, "c.py", dispatch_id=child_id)
    closeout = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    rows = enumerate_tip_window_commits(
        tmp_path, admit_head=admit, closeout_head=closeout
    )
    authored, _unfiltered = partition_tip_window_meters(rows, dispatch_id=parent_id)
    refs = observed_lane_git_refs(
        tmp_path,
        dispatch_id=parent_id,
        admit_head=admit,
        closeout_head=closeout,
    )
    assert authored == refs


@pytest.mark.parametrize(
    ("lane", "admit_head", "ancestry", "authored_meter", "expected_landed"),
    [
        pytest.param("A", "abc", True, 2, True, id="L1"),
        pytest.param("A", "abc", True, 0, False, id="L2"),
        pytest.param("A", None, True, None, None, id="L3"),
        pytest.param("A", "abc", False, 3, False, id="L4"),
        pytest.param("A", "abc", None, 1, None, id="L5"),
        pytest.param(None, "abc", True, 2, True, id="L7"),
        pytest.param(None, "abc", True, 0, False, id="L8"),
        pytest.param(None, None, True, None, None, id="L9"),
    ],
)
def test_landed_matrix_non_b(
    tmp_path: Path,
    lane: str | None,
    admit_head: str | None,
    ancestry: bool | None,
    authored_meter: int | None,
    expected_landed: bool | None,
) -> None:
    """AC4 L1–L5, L7–L9: non-B structured ``landed`` from authored meter + ancestry."""
    head = _init_git_repo_with_commit(tmp_path) if admit_head == "abc" else None
    commits_ahead = authored_meter
    commits_unfiltered = authored_meter if authored_meter is not None else None
    if commits_ahead is not None and lane != "B":
        landed = admit_landed_true(
            ancestry_on_master=ancestry,
            commits_ahead=commits_ahead,
        )
    else:
        landed = None
    assert landed == expected_landed

    body = build_implement_closeout_body(
        dispatch_id="landed-matrix",
        outcome=_outcome(),
        degraded_reason=None,
        sidecar_ref="workspaces://universal-llm-gateway/tmp/reviews/x.md",
        result_bytes=100,
        thread_id="t-lm",
        work_item_ref=None,
        lane=lane,
        head_sha=head,
        commits_ahead=commits_ahead,
        commits_ahead_unfiltered=commits_unfiltered,
        landed=landed,
        deliverables_expected=True,
    )
    payload = json.loads(body)
    if commits_ahead is None:
        assert "commits_ahead" not in payload
        assert "commits_ahead_unfiltered" not in payload
    else:
        assert payload["commits_ahead"] == commits_ahead
        assert payload["commits_ahead_unfiltered"] == commits_unfiltered
    if expected_landed is None:
        assert payload.get("landed") is None
    else:
        assert payload["landed"] is expected_landed


def _init_git_repo_with_commit(path: Path) -> str:
    _init_git_repo(path)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_landed_matrix_l1_live_tip_on_master(tmp_path: Path) -> None:
    """AC4 L1: Lane-A tip on master with authored>=1 → landed true."""
    dispatch_id = "l1-live"
    admit = _init_git_repo_with_commit(tmp_path)
    _commit(tmp_path, "l1.py", dispatch_id=dispatch_id)
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-l1",
        work_item_ref=None,
        baseline={"admit_head": admit},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["commits_ahead"] >= 1
    assert payload["landed"] is True


def test_landed_matrix_l2_live_zero_commits(tmp_path: Path) -> None:
    """AC4 L2: Lane-A empty range → landed false."""
    admit = _init_git_repo_with_commit(tmp_path)
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="l2-live",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-l2",
        work_item_ref=None,
        baseline={"admit_head": admit},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["commits_ahead"] == 0
    assert payload["landed"] is False


def test_falsifier_both_keys_present_together(tmp_path: Path) -> None:
    """AC5 (b): unfiltered absent while authored present is forbidden."""
    dispatch_id = "fals-b"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    _commit(tmp_path, "x.py", dispatch_id=dispatch_id)
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-fb",
        work_item_ref=None,
        baseline={"admit_head": admit},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert "commits_ahead" in payload
    assert "commits_ahead_unfiltered" in payload


def test_falsifier_c_landed_not_null_when_meter_numeric(tmp_path: Path) -> None:
    """AC5 (c): structured landed set when authored meter numeric on master tip."""
    dispatch_id = "fals-c"
    admit = _init_git_repo_with_commit(tmp_path)
    _commit(tmp_path, "fc.py", dispatch_id=dispatch_id)
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-fc",
        work_item_ref=None,
        baseline={"admit_head": admit},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert isinstance(payload["commits_ahead"], int)
    assert payload["landed"] is not None
