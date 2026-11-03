"""Friction 41885 — a worker land claim must not block silent land or survive a false probe."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    maybe_ff_land_silent_lane,
)
from services.git_integration_worker.tests.land_k43_fixture import exempt_branch

pytestmark = pytest.mark.offline

_UNMARKED_LAND_CLAIM = re.compile(
    r"(?im)^[ \t]*(?:landed:[ \t]+\S+|land_disposition:[ \t]*[`\"']?landed\b)"
)


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


def _one_commit_lane(repo: Path, branch: str) -> str:
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / "lane.md").write_text("lane\n", encoding="utf-8")
    assert _git(repo, "add", "lane.md").returncode == 0
    assert _git(repo, "commit", "-m", "lane").returncode == 0
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "checkout", "master").returncode == 0
    return tip


def test_worker_landed_disposition_does_not_block_silent_land(tmp_path: Path) -> None:
    """A bare ``land: silent`` packet still fast-forwards when the worker claims landed."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-f41885-claim"
    tip = _one_commit_lane(repo, branch)
    exempt_branch(repo, branch)
    landed = maybe_ff_land_silent_lane(
        repo=repo,
        branch_name=branch,
        dispatch_id="d-f41885-landed-claim",
        packet_text="land: silent\n",
        closeout_text="land_disposition: landed\nstatus: complete\n",
        commits_ahead=1,
    )
    assert landed is True
    ancestor = _git(repo, "merge-base", "--is-ancestor", tip, "master")
    assert ancestor.returncode == 0


def test_on_master_worker_claim_is_replaced_by_probe_lines(tmp_path: Path) -> None:
    """Probe-true receipt publishes ``landed:`` only for the sha that is on master."""
    from services.git_integration_worker.relay.closeout_plane_probe import (
        project_receipt_land_claims,
    )

    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-f41885-on"
    tip = _one_commit_lane(repo, branch)
    assert _git(repo, "merge", "--ff-only", branch).returncode == 0
    worker = "landed: deadbeef0123456789abcdef0123456789\nstatus: complete\n"
    receipt = project_receipt_land_claims(
        worker,
        repo=repo,
        sha=tip,
        branch=branch,
    )
    assert f"landed: {tip}" in receipt
    assert "land_disposition: landed" in receipt
    assert "deadbeef0123456789abcdef0123456789" not in receipt
    assert "off master" not in receipt.lower()


def test_off_master_worker_landed_line_is_not_an_unmarked_claim(tmp_path: Path) -> None:
    """Probe-false receipt drops worker ``landed:`` and states the tip is off master."""
    from services.git_integration_worker.relay.closeout_plane_probe import (
        project_receipt_land_claims,
    )

    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-f41885-off"
    tip = _one_commit_lane(repo, branch)
    worker = f"landed: {tip}\nstatus: complete\n"
    receipt = project_receipt_land_claims(
        worker,
        repo=repo,
        sha=tip,
        branch=branch,
    )
    assert _UNMARKED_LAND_CLAIM.search(receipt) is None
    assert "off master" in receipt.lower()
    assert f"landed: {tip}" not in receipt
