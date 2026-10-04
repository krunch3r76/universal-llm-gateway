"""Hub land actuators refuse to move master unless this process holds the lease."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    maybe_ff_land_silent_lane,
)
from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    can_ff_onto_hub_master,
    clean_merge_onto_hub_master,
    ff_only_onto_hub_master,
)
from services.git_integration_worker.cursor_sdk_land_lease import (
    master_land_lease_key,
    release_land_lease,
    try_acquire_land_lease,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_hub(tmp_path: Path) -> Path:
    repo = tmp_path / "hub"
    repo.mkdir()
    assert _git(repo, "init", "-b", "master").returncode == 0
    assert _git(repo, "config", "user.email", "t@example.com").returncode == 0
    assert _git(repo, "config", "user.name", "t").returncode == 0
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    assert _git(repo, "add", "README.md").returncode == 0
    assert _git(repo, "commit", "-m", "seed").returncode == 0
    return repo


def _commit_on_branch(repo: Path, branch: str, body: str) -> None:
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / "lane.md").write_text(body, encoding="utf-8")
    assert _git(repo, "add", "lane.md").returncode == 0
    assert _git(repo, "commit", "-m", branch).returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0


def _master(repo: Path) -> str:
    return _git(repo, "rev-parse", "refs/heads/master").stdout.strip()


def test_ff_only_onto_hub_master_without_lease_leaves_master_unchanged(
    tmp_path: Path,
) -> None:
    repo = _init_hub(tmp_path)
    _commit_on_branch(repo, "cursor-sdk/lane-nolease", "lane\n")
    before = _master(repo)
    assert ff_only_onto_hub_master(repo, branch_name="cursor-sdk/lane-nolease") is False
    assert _master(repo) == before


def test_clean_merge_onto_hub_master_without_lease_leaves_master_unchanged(
    tmp_path: Path,
) -> None:
    repo = _init_hub(tmp_path)
    assert _git(repo, "checkout", "-b", "peer").returncode == 0
    (repo / "peer.md").write_text("peer\n", encoding="utf-8")
    assert _git(repo, "add", "peer.md").returncode == 0
    assert _git(repo, "commit", "-m", "peer").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    (repo / "hub.md").write_text("hub\n", encoding="utf-8")
    assert _git(repo, "add", "hub.md").returncode == 0
    assert _git(repo, "commit", "-m", "hub").returncode == 0
    before = _master(repo)
    assert clean_merge_onto_hub_master(repo, branch_name="peer") is False
    assert _master(repo) == before


def test_actuator_from_other_process_cannot_move_master(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    _commit_on_branch(repo, "cursor-sdk/lane-otherpid", "lane\n")
    key = master_land_lease_key(repo)
    holder = "other-land"
    assert try_acquire_land_lease(lease_key=key, holder_op_id=holder)
    before = _master(repo)
    try:
        env = os.environ.copy()
        root = str(Path(__file__).resolve().parents[3])
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import os; "
                "from services.git_integration_worker.cursor_sdk_hub_land_scope "
                "import ff_only_onto_hub_master; "
                "print(ff_only_onto_hub_master("
                "Path(os.environ['HUB']), "
                "branch_name='cursor-sdk/lane-otherpid'))",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**env, "HUB": str(repo)},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False"
        assert _master(repo) == before
    finally:
        release_land_lease(lease_key=key, holder_op_id=holder)


def test_can_ff_predicate_leaves_repo_unchanged(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    assert _git(repo, "checkout", "-b", "cursor-sdk/lane-div").returncode == 0
    (repo / "div.md").write_text("div\n", encoding="utf-8")
    assert _git(repo, "add", "div.md").returncode == 0
    assert _git(repo, "commit", "-m", "div").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    (repo / "master-only.md").write_text("m\n", encoding="utf-8")
    assert _git(repo, "add", "master-only.md").returncode == 0
    assert _git(repo, "commit", "-m", "master only").returncode == 0
    _commit_on_branch(repo, "cursor-sdk/lane-ff", "ff\n")
    before = _master(repo)
    index = _git(repo, "write-tree").stdout.strip()
    porcelain = _git(repo, "status", "--porcelain=v1").stdout
    assert can_ff_onto_hub_master(repo, branch_name="cursor-sdk/lane-ff") is True
    assert can_ff_onto_hub_master(repo, branch_name="cursor-sdk/lane-div") is False
    assert _master(repo) == before
    assert _git(repo, "write-tree").stdout.strip() == index
    assert _git(repo, "status", "--porcelain=v1").stdout == porcelain


def test_silent_land_acquires_lease_when_free_and_skips_when_held(
    tmp_path: Path,
) -> None:
    repo = _init_hub(tmp_path)
    _commit_on_branch(repo, "cursor-sdk/lane-silent", "silent\n")
    before = _master(repo)
    key = master_land_lease_key(repo)
    holder = "busy-land"
    assert try_acquire_land_lease(lease_key=key, holder_op_id=holder)
    try:
        blocked = maybe_ff_land_silent_lane(
            repo=repo,
            branch_name="cursor-sdk/lane-silent",
            dispatch_id="d-blocked",
            packet_text="land: silent\n",
            closeout_text="no disposition",
            commits_ahead=1,
        )
        assert blocked is False
        assert _master(repo) == before
    finally:
        release_land_lease(lease_key=key, holder_op_id=holder)
    landed = maybe_ff_land_silent_lane(
        repo=repo,
        branch_name="cursor-sdk/lane-silent",
        dispatch_id="d-free",
        packet_text="land: silent\n",
        closeout_text="no disposition",
        commits_ahead=1,
    )
    assert landed is True
    assert _master(repo) != before
