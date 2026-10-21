"""Friction 39056-B — ``land_disposition: unlanded <tip>`` matching branch tip allows silent land."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    maybe_ff_land_silent_lane,
)
from services.git_integration_worker.tests.land_k43_fixture import exempt_branch

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


def test_silent_land_proceeds_when_unlanded_tip_matches_branch(
    tmp_path: Path,
) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-unlanded-tip"
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / "lane.md").write_text("lane\n", encoding="utf-8")
    assert _git(repo, "add", "lane.md").returncode == 0
    assert _git(repo, "commit", "-m", "lane").returncode == 0
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    short = tip[:12]
    assert _git(repo, "checkout", "master").returncode == 0
    master_before = _git(repo, "rev-parse", "master").stdout.strip()
    closeout = f"land_disposition: unlanded {short}\nstatus: complete\n"
    packet = "land: silent\n"
    exempt_branch(repo, branch)
    landed = maybe_ff_land_silent_lane(
        repo=repo,
        branch_name=branch,
        dispatch_id="d-unlanded-agree",
        packet_text=packet,
        closeout_text=closeout,
        commits_ahead=1,
    )
    assert landed is True
    assert _git(repo, "rev-parse", "master").stdout.strip() != master_before
    ancestor = _git(repo, "merge-base", "--is-ancestor", tip, "master")
    assert ancestor.returncode == 0


def test_silent_land_refuses_unlanded_full_sha_same_seven_char_prefix_only(
    tmp_path: Path,
) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-seven-prefix"
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / "lane.md").write_text("lane\n", encoding="utf-8")
    assert _git(repo, "add", "lane.md").returncode == 0
    assert _git(repo, "commit", "-m", "lane").returncode == 0
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    forged = tip[:7] + ("a" * 33)
    if forged.lower() == tip.lower():
        forged = tip[:7] + ("b" * 33)
    assert forged[:7] == tip[:7]
    assert forged.lower() != tip.lower()
    assert _git(repo, "checkout", "master").returncode == 0
    master_before = _git(repo, "rev-parse", "master").stdout.strip()
    closeout = f"land_disposition: unlanded {forged}\n"
    landed = maybe_ff_land_silent_lane(
        repo=repo,
        branch_name=branch,
        dispatch_id="d-seven-prefix",
        packet_text="land: silent\n",
        closeout_text=closeout,
        commits_ahead=1,
    )
    assert landed is False
    assert _git(repo, "rev-parse", "master").stdout.strip() == master_before


def test_silent_land_refuses_unlanded_tip_mismatch(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-unlanded-mismatch"
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / "lane.md").write_text("lane\n", encoding="utf-8")
    assert _git(repo, "add", "lane.md").returncode == 0
    assert _git(repo, "commit", "-m", "lane").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    master_before = _git(repo, "rev-parse", "master").stdout.strip()
    closeout = "land_disposition: unlanded deadbeef0123456789abcdef0123456789\n"
    landed = maybe_ff_land_silent_lane(
        repo=repo,
        branch_name=branch,
        dispatch_id="d-unlanded-mismatch",
        packet_text="land: silent\n",
        closeout_text=closeout,
        commits_ahead=1,
    )
    assert landed is False
    assert _git(repo, "rev-parse", "master").stdout.strip() == master_before


def test_silent_land_still_refuses_discard_disposition(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-discard-block"
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / "lane.md").write_text("lane\n", encoding="utf-8")
    assert _git(repo, "add", "lane.md").returncode == 0
    assert _git(repo, "commit", "-m", "lane").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    master_before = _git(repo, "rev-parse", "master").stdout.strip()
    landed = maybe_ff_land_silent_lane(
        repo=repo,
        branch_name=branch,
        dispatch_id="d-discard",
        packet_text="land: silent\n",
        closeout_text="land_disposition: discard\nland_reason: scratch\n",
        commits_ahead=1,
    )
    assert landed is False
    assert _git(repo, "rev-parse", "master").stdout.strip() == master_before
