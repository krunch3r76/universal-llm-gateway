"""Hub silent land with disjoint-only dirty master (parity manual S1a land)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    settle_lane_branch,
)
from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    hub_land_precondition_reason,
    land_lane_branch_onto_hub_master,
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


def _commit_on_branch(repo: Path, branch: str, rel: str, body: str) -> None:
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / rel).write_text(body, encoding="utf-8")
    assert _git(repo, "add", rel).returncode == 0
    assert _git(repo, "commit", "-m", branch).returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0


def _master(repo: Path) -> str:
    return _git(repo, "rev-parse", "refs/heads/master").stdout.strip()


def test_hub_land_refuses_gitignored_path_added_then_removed_at_tip(
    tmp_path: Path,
) -> None:
    repo = _init_hub(tmp_path)
    (repo / ".gitignore").write_text("ignored/\n**/*.wiki.md\n", encoding="utf-8")
    assert _git(repo, "add", ".gitignore").returncode == 0
    assert _git(repo, "commit", "-m", "gitignore wiki").returncode == 0
    branch = "cursor-sdk/lane-gitignore-history"
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    rel = "docs/foo.wiki.md"
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text("ignored body\n", encoding="utf-8")
    assert _git(repo, "add", "-f", rel).returncode == 0
    assert _git(repo, "commit", "-m", "add gitignored path").returncode == 0
    assert _git(repo, "rm", "--cached", rel).returncode == 0
    assert _git(repo, "commit", "-m", "untrack at tip").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    assert hub_land_precondition_reason(repo, branch_name=branch) == (
        "hub_lane_gitignored_path"
    )
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-gitignore-history"
    )
    assert result.landed is False
    assert result.refuse_reason == "hub_lane_gitignored_path"
    assert _master(repo) == before


def test_hub_land_paths_unknown_when_branch_diff_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-paths-unknown"
    _commit_on_branch(repo, branch, "lane.md", "lane\n")
    (repo / "foreign-wip.md").write_text("peer wip\n", encoding="utf-8")
    import services.git_integration_worker.cursor_sdk_hub_land_scope as scope

    real_git_capture = scope._git_capture

    def _git_capture_stub(hub: Path, *args: str):
        if args == ("diff", "--name-only", f"master...{branch}"):
            return subprocess.CompletedProcess(args, 1, "", "fatal: bad ref")
        return real_git_capture(hub, *args)

    monkeypatch.setattr(scope, "_git_capture", _git_capture_stub)
    assert hub_land_precondition_reason(repo, branch_name=branch) == (
        "hub_land_paths_unknown"
    )
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-paths-unknown"
    )
    assert result.landed is False
    assert result.refuse_reason == "hub_land_paths_unknown"
    assert _master(repo) == before


def test_disjoint_dirty_hub_logs_paths_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-disjoint-log"
    _commit_on_branch(repo, branch, "lane.md", "lane\n")
    (repo / "foreign-wip.md").write_text("peer wip\n", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        land_lane_branch_onto_hub_master(
            repo, branch_name=branch, holder_op_id="d-disjoint-log"
        )
    assert any(
        "hub land proceed disjoint dirty_paths=" in r.message
        and "foreign-wip.md" in r.message
        for r in caplog.records
    )


def test_overlapping_dirty_hub_logs_paths_on_refuse(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-overlap-log"
    _commit_on_branch(repo, branch, "lane.md", "lane tip\n")
    (repo / "lane.md").write_text("hub divergent\n", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        result = land_lane_branch_onto_hub_master(
            repo, branch_name=branch, holder_op_id="d-overlap-log"
        )
    assert result.refuse_reason == "hub_dirty_overlap"
    assert any(
        "hub land refuse hub_dirty_overlap dirty_paths=" in r.message
        for r in caplog.records
    )


def test_disjoint_dirty_hub_allows_silent_land(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-disjoint"
    _commit_on_branch(repo, branch, "lane.md", "lane\n")
    before = _master(repo)
    foreign = repo / "foreign-wip.md"
    foreign.write_text("peer wip\n", encoding="utf-8")
    wip_before = foreign.read_text(encoding="utf-8")
    wip_porcelain_before = _git(repo, "status", "--porcelain", "--", "foreign-wip.md").stdout

    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-disjoint"
    )
    assert result.landed is True
    assert _master(repo) != before
    assert foreign.read_text(encoding="utf-8") == wip_before
    assert _git(repo, "status", "--porcelain", "--", "foreign-wip.md").stdout == wip_porcelain_before


def test_overlapping_dirty_hub_refuses_with_reason(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-overlap"
    _commit_on_branch(repo, branch, "lane.md", "lane tip\n")
    before = _master(repo)
    lane_path = repo / "lane.md"
    lane_path.write_text("hub divergent\n", encoding="utf-8")
    before_bytes = lane_path.read_bytes()

    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-overlap"
    )
    assert result.landed is False
    assert result.refuse_reason == "hub_dirty_overlap"
    assert _master(repo) == before
    assert lane_path.read_bytes() == before_bytes


def test_staged_foreign_file_refuses_hub_index_staged(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-staged"
    _commit_on_branch(repo, branch, "lane.md", "lane\n")
    (repo / "staged-foreign.py").write_text("x\n", encoding="utf-8")
    assert _git(repo, "add", "staged-foreign.py").returncode == 0
    assert hub_land_precondition_reason(repo, branch_name=branch) == "hub_index_staged"


def test_head_not_master_refuses(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-head"
    _commit_on_branch(repo, branch, "lane.md", "lane\n")
    assert _git(repo, "checkout", branch).returncode == 0
    assert hub_land_precondition_reason(repo, branch_name=branch) == "hub_head_not_master"


def test_not_ff_reason_when_ff_fails_without_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_hub(tmp_path)
    assert _git(repo, "checkout", "-b", "cursor-sdk/lane-div").returncode == 0
    (repo / "div.md").write_text("div\n", encoding="utf-8")
    assert _git(repo, "add", "div.md").returncode == 0
    assert _git(repo, "commit", "-m", "div").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    (repo / "master-only.md").write_text("m\n", encoding="utf-8")
    assert _git(repo, "add", "master-only.md").returncode == 0
    assert _git(repo, "commit", "-m", "master only").returncode == 0
    import services.git_integration_worker.cursor_sdk_hub_land_scope as scope

    monkeypatch.setattr(
        scope,
        "_clean_merge_onto_hub_master",
        lambda *_a, **_k: (False, None),
    )
    result = land_lane_branch_onto_hub_master(
        repo, branch_name="cursor-sdk/lane-div", holder_op_id="d-not-ff"
    )
    assert result.landed is False
    assert result.refuse_reason == "not_ff"


def test_merge_conflict_reason(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    assert _git(repo, "checkout", "-b", "cursor-sdk/lane-conflict").returncode == 0
    (repo / "both.md").write_text("lane side\n", encoding="utf-8")
    assert _git(repo, "add", "both.md").returncode == 0
    assert _git(repo, "commit", "-m", "lane").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    (repo / "both.md").write_text("master side\n", encoding="utf-8")
    assert _git(repo, "add", "both.md").returncode == 0
    assert _git(repo, "commit", "-m", "master").returncode == 0
    result = land_lane_branch_onto_hub_master(
        repo, branch_name="cursor-sdk/lane-conflict", holder_op_id="d-conflict"
    )
    assert result.landed is False
    assert result.refuse_reason == "merge_conflict"


def test_settle_debt_reason_land_hub_dirty_overlap(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-debt"
    _commit_on_branch(repo, branch, "lane.md", "lane\n")
    (repo / "lane.md").write_text("dirty overlap\n", encoding="utf-8")
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
            thread_id="15760",
            dispatch_id="d-debt",
            closeout_text=None,
            commits_ahead=1,
            landed=False,
            head_sha=_git(repo, "rev-parse", branch).stdout.strip(),
            packet_text="land: silent\n",
        )
    assert settlement.outcome == "debt_opened"
    assert emitted
    assert emitted[-1].get("reason") == "land:hub_dirty_overlap"
