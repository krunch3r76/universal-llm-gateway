"""Friction 41952 — vacuous on-master tips and silent-land refusals.

Receipt projection must not publish a land claim when the lane is zero commits
ahead of its branch point. A scoped-out silent packet must not be recorded as
a dirty hub. Callers that omit the ahead-meter keep the ancestry-only path.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    settle_lane_branch,
)
from services.git_integration_worker.relay.closeout_plane_probe import (
    project_receipt_land_claims,
)

pytestmark = pytest.mark.offline

_SCOPE_OUT = "do not hub-land"


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


def _project(repo: Path, branch: str, tip: str, **meter: Any) -> str:
    """Call the projector, supplying the ahead-meter only once the signature admits it."""
    kwargs: dict[str, Any] = {"repo": repo, "sha": tip, "branch": branch}
    params = inspect.signature(project_receipt_land_claims).parameters
    if "commits_ahead" in params:
        kwargs.update(meter)
    return project_receipt_land_claims("landed: x\n", **kwargs)


def test_zero_ahead_tip_on_master_does_not_publish_landed(tmp_path: Path) -> None:
    """A branch point already on master must not be published as a land."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-f41952-vacuous"
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "checkout", "master").returncode == 0
    receipt = _project(
        repo,
        branch,
        tip,
        commits_ahead=0,
        commits_ahead_presence="present",
        git_land_plane_uncomputable=False,
    )
    assert "land_disposition: landed" not in receipt


def test_uncomputable_zero_ahead_stays_unverified(tmp_path: Path) -> None:
    """An uncomputable plane leaves the worker line unverified, not published."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-f41952-uncomputable"
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "checkout", "master").returncode == 0
    receipt = _project(
        repo,
        branch,
        tip,
        commits_ahead=0,
        commits_ahead_presence="present",
        git_land_plane_uncomputable=True,
    )
    assert "land_disposition: landed" not in receipt
    assert "unverified worker claim:" in receipt


def test_scoped_out_silent_debt_is_not_hub_dirty(tmp_path: Path) -> None:
    """A silent packet that scopes out hub land records that refusal, not a dirty hub."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-f41952-scoped"
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / "lane.md").write_text("lane\n", encoding="utf-8")
    assert _git(repo, "add", "lane.md").returncode == 0
    assert _git(repo, "commit", "-m", "lane").returncode == 0
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "checkout", "master").returncode == 0
    emitted: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        emitted.append(kwargs)

    with patch(
        "services.git_integration_worker.cursor_sdk_branch_terminal.emit_sdk_lane_b_debt_opened",
        side_effect=_capture,
    ):
        settlement = settle_lane_branch(
            source_repo=repo,
            branch_name=branch,
            thread_id="22152",
            dispatch_id="d-f41952-scoped",
            closeout_text="status: complete\n",
            commits_ahead=1,
            landed=False,
            head_sha=tip,
            packet_text=f"land: silent\nintent: {_SCOPE_OUT}\n",
        )
    assert settlement.outcome == "debt_opened"
    assert emitted
    assert emitted[-1].get("reason") != "land:hub_dirty"
    assert emitted[-1].get("reason") == "land:scoped_out"
