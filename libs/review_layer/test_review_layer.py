"""Unit tests for review_layer detection, git_io, and lane resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from review_layer.detect import (
    detect_review_targets,
    implicit_lane_thread_ids,
    resolve_primary_target,
)
from review_layer.git_io import (
    ReviewLayerInfraError,
    ReviewTargetUnreachable,
    resolve_commit,
)
from review_layer.materialize import (
    lane_worktree_path,
    resolve_review_target,
)


def test_review_thread_explicit() -> None:
    body = "review_thread: 22520\nreview_head: feature-tip\n"
    result = detect_review_targets(body)
    t = result.targets[0]
    assert t.kind == "review_thread"
    assert t.thread_id == "22520"
    assert t.head == "feature-tip"


def test_lane_branch_anchor() -> None:
    body = "Branch cursor-sdk/lane-22520 carries the diff."
    t = resolve_primary_target(detect_review_targets(body))
    assert t is not None
    assert t.thread_id == "22520"


def test_lane_suffix_not_matched() -> None:
    assert detect_review_targets("cursor-sdk/lane-22520-r2").targets == ()


def test_multiline_review_head_not_captured() -> None:
    body = "review_head:\ndeadbeef\n"
    assert detect_review_targets(body).explicit_head is None


def test_ambiguous_implicit_threads() -> None:
    body = "cursor-sdk/lane-1 and cursor-sdk/lane-2"
    result = detect_review_targets(body)
    assert len(implicit_lane_thread_ids(result)) == 2
    assert resolve_primary_target(result) is None


def test_review_thread_wins_over_implicit() -> None:
    body = "cursor-sdk/lane-99\nreview_thread: 1\n"
    t = resolve_primary_target(detect_review_targets(body))
    assert t is not None
    assert t.thread_id == "1"


def _init_lane_repo(repo: Path, thread: str) -> None:
    subprocess.check_call(["git", "init", "-b", "master"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@t.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("m\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-qm", "m"], cwd=repo)
    branch = f"cursor-sdk/lane-{thread}"
    subprocess.check_call(["git", "checkout", "-b", branch], cwd=repo)
    (repo / "lane.txt").write_text("lane\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "lane.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-qm", "lane"], cwd=repo)
    subprocess.check_call(["git", "checkout", "master"], cwd=repo)


def test_resolve_review_target_with_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt_root = tmp_path / "wt"
    repo.mkdir()
    wt_root.mkdir()
    _init_lane_repo(repo, "9")
    lane_dir = lane_worktree_path("9", wt_root)
    lane_dir.mkdir(parents=True)

    resolved = resolve_review_target(
        thread_id="9",
        head_ref=None,
        base_ref=None,
        repo=repo,
        worktree_root_path=wt_root,
    )
    assert resolved is not None
    assert resolved["thread"] == "9"
    assert resolved["branch"] == "cursor-sdk/lane-9"


def test_resolve_merged_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_lane_repo(repo, "1")
    subprocess.check_call(["git", "merge", "cursor-sdk/lane-1", "-m", "m"], cwd=repo)
    assert (
        resolve_review_target(
            thread_id="1",
            head_ref=None,
            base_ref=None,
            repo=repo,
            worktree_root_path=tmp_path / "wt",
        )
        is None
    )


def test_missing_worktree_target_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_lane_repo(repo, "2")
    with pytest.raises(ReviewTargetUnreachable):
        resolve_review_target(
            thread_id="2",
            head_ref=None,
            base_ref=None,
            repo=repo,
            worktree_root_path=tmp_path / "wt",
        )


def test_bogus_ref_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_lane_repo(repo, "3")
    with pytest.raises(ReviewTargetUnreachable):
        resolve_commit(repo, "no-such-ref")


def test_non_repo_infra(tmp_path: Path) -> None:
    with pytest.raises(ReviewLayerInfraError):
        resolve_commit(tmp_path / "not-git", "master")


def test_format_review_target_lists_fs_calls(tmp_path: Path) -> None:
    from review_layer.materialize import format_review_target_block

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_lane_repo(repo, "5")
    head = resolve_commit(repo, "cursor-sdk/lane-5")
    base = resolve_commit(repo, "master")
    block = format_review_target_block(
        thread_id="5",
        branch="cursor-sdk/lane-5",
        base_sha=base,
        head_sha=head,
        repo=repo,
    )
    assert 'thread="5"' in block
    assert "universal-llm-gateway/lane.txt" in block


def test_validate_git_ref_rejects_dash() -> None:
    from review_layer.git_io import validate_git_ref

    with pytest.raises(ReviewTargetUnreachable):
        validate_git_ref("-dirty")
