"""arc 12286 — qualify bare ``land_disposition: landed`` with plane vocabulary (13157 specimen)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
    inject_qualified_land_disposition,
)
from services.git_integration_worker.cursor_auto.closeout_tree_state import (
    compute_closeout_tree_state,
)
from services.git_integration_worker.cursor_sdk_branch_terminal import (
    parse_land_disposition,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
    ).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "master"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"],
        check=True,
    )
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    return repo


def _wrapper(*, head_sha: str, branch: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "status": "complete",
            "capture_status": "complete",
            "files_created": [],
            "head_sha": head_sha,
            "branch": branch,
            "commits_ahead": 1,
            "landed": False,
        }
    )


def test_specimen_13157_shape_plane_and_land_disposition_agree(tmp_path: Path) -> None:
    """AC2 — lane tip with NOT landed@local-master; bare landed no longer contradicts plane."""
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/lane-13157"
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", branch], check=True)
    (repo / "x.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-m", "lane work")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    wrapper = _wrapper(head_sha=head, branch=branch)
    with (
        patch(
            "services.git_integration_worker.cursor_auto.closeout_tree_state."
            "compute_lane_a_checkpoint_value",
            return_value="nothing_authored",
        ),
        patch(
            "services.git_integration_worker.cursor_auto.closeout_tree_state."
            "authored_paths_for_dispatch",
            return_value=(),
        ),
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="auto-3a19a8b5c4e4",
            wrapper_text=wrapper,
        )
    assert "NOT landed@local-master" in state.plane_line
    assert state.land_disposition_measurement is not None
    assert "NOT landed@local-master" in state.land_disposition_measurement
    assert "tip@lane-B" in state.land_disposition_measurement
    assert branch in state.land_disposition_measurement

    body = (
        "status: complete\n"
        "checkpoint: nothing_authored@local-master\n"
        "land_disposition: landed\n"
    )
    amended = inject_qualified_land_disposition(
        body,
        measurement=state.land_disposition_measurement,
    )
    assert "land_disposition: landed\n" not in amended
    assert (
        f"land_disposition: {state.land_disposition_measurement}" in amended
    )
    assert "NOT landed@local-master" in amended
    for token in state.land_disposition_measurement.split(" · "):
        assert token in state.plane_line


def test_genuine_master_land_qualifies_to_landed_at_local_master(tmp_path: Path) -> None:
    """AC1 — real master land keeps landed semantics with explicit plane token."""
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/lane-ok"
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", branch], check=True)
    (repo / "y.py").write_text("y\n", encoding="utf-8")
    _git(repo, "add", "y.py")
    _git(repo, "commit", "-m", "land")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    _git(repo, "merge", "--ff-only", branch)
    wrapper = _wrapper(head_sha=head, branch=branch)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="auto-master-land",
            wrapper_text=wrapper,
        )
    assert "landed@local-master" in state.plane_line
    assert state.land_disposition_measurement == "landed@local-master"
    amended = inject_qualified_land_disposition(
        "land_disposition: landed\n",
        measurement=state.land_disposition_measurement,
    )
    assert amended.strip() == "land_disposition: landed@local-master"


@pytest.mark.parametrize(
    ("line", "expected_verb"),
    [
        ("land_disposition: landed@local-master", "landed"),
        (
            "land_disposition: tip@lane-B(cursor-sdk/lane-13157) · NOT landed@local-master",
            "landed",
        ),
        ("land_disposition: discard", "discard"),
    ],
)
def test_parse_land_disposition_reads_qualified_landed(line: str, expected_verb: str) -> None:
    verb, _reason, _sha = parse_land_disposition(line)
    assert verb == expected_verb
