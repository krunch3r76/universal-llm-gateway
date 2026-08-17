"""Git HEAD / lane commit path helpers (6341 own-commit attribution)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_home import dispatch_git_identity
from services.git_integration_worker.cursor_sdk_git_head import (
    enumerate_tip_window_commits,
    observed_lane_git_refs,
    partition_tip_window_meters,
    paths_exclusive_to_lane,
    paths_in_commit,
    with_head_sha_fallback,
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
    proc = subprocess.run(cmd, check=True, capture_output=True, env=env)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    )
    return head.stdout.decode().strip()


def test_paths_exclusive_to_lane_excludes_peer_touch(tmp_path: Path) -> None:
    dispatch_id = "d-excl"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    rel = "touch.py"
    _commit(tmp_path, rel, dispatch_id=dispatch_id)
    lane_head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout.decode().strip()
    (tmp_path / rel).write_text("# peer\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", rel], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "peer"],
        check=True,
        capture_output=True,
    )
    closeout = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout.decode().strip()
    lane_refs = observed_lane_git_refs(
        tmp_path,
        dispatch_id=dispatch_id,
        admit_head=admit,
        closeout_head=closeout,
    )
    assert len(lane_refs) == 1
    assert rel in paths_in_commit(tmp_path, lane_refs[0])
    exclusive = paths_exclusive_to_lane(
        tmp_path,
        dispatch_id=dispatch_id,
        admit_head=admit,
        closeout_head=closeout,
    )
    assert rel not in exclusive


def test_paths_exclusive_to_lane_includes_sole_lane_path(tmp_path: Path) -> None:
    dispatch_id = "d-sole"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    rel = "lane_only.py"
    _commit(tmp_path, rel, dispatch_id=dispatch_id)
    closeout = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout.decode().strip()
    exclusive = paths_exclusive_to_lane(
        tmp_path,
        dispatch_id=dispatch_id,
        admit_head=admit,
        closeout_head=closeout,
    )
    assert rel in exclusive


def _rev_parse(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _cherrypick_as_lander(
    repo: Path, *, source_sha: str, lander_id: str
) -> str:
    """Cherry-pick *source_sha* onto current HEAD, stamping *lander_id* as committer."""
    name, email = dispatch_git_identity(lander_id)
    env = dict(os.environ)
    env.update(
        {
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
    )
    subprocess.run(
        ["git", "-C", str(repo), "cherry-pick", source_sha],
        check=True,
        capture_output=True,
        env=env,
    )
    return _rev_parse(repo)


def test_observed_lane_git_refs_attributes_cherrypick_to_lander(
    tmp_path: Path,
) -> None:
    """AC-1 — lander committer match sees the cherry-pick SHA; --author= would miss it."""
    source_id = "auto-02322b616e34"
    lander_id = "auto-3f33c027a588"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "src"],
        check=True,
        capture_output=True,
    )
    source_sha = _commit(tmp_path, "land.py", dispatch_id=source_id)
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "master"],
        check=True,
        capture_output=True,
    )
    land_sha = _cherrypick_as_lander(
        tmp_path, source_sha=source_sha, lander_id=lander_id
    )
    closeout = land_sha
    lander_email = dispatch_git_identity(lander_id)[1]
    author_only = subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "log",
            f"{admit}..{closeout}",
            f"--author={lander_email}",
            "--format=%H",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert author_only == ""
    refs = observed_lane_git_refs(
        tmp_path,
        dispatch_id=lander_id,
        admit_head=admit,
        closeout_head=closeout,
    )
    assert refs == [land_sha]


def test_observed_lane_git_refs_empty_when_dispatch_landed_nothing(
    tmp_path: Path,
) -> None:
    """AC-3 — a dispatch that produced no commit in the window still reports none."""
    lander_id = "auto-3f33c027a588"
    stranger_id = "auto-stranger-no-land"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "src"],
        check=True,
        capture_output=True,
    )
    source_sha = _commit(tmp_path, "land.py", dispatch_id="auto-02322b616e34")
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "master"],
        check=True,
        capture_output=True,
    )
    closeout = _cherrypick_as_lander(
        tmp_path, source_sha=source_sha, lander_id=lander_id
    )
    refs = observed_lane_git_refs(
        tmp_path,
        dispatch_id=stranger_id,
        admit_head=admit,
        closeout_head=closeout,
    )
    assert refs == []


def test_partition_meter_stays_author_only_on_cherrypick(tmp_path: Path) -> None:
    """AC-4 — plane authored meter does not inherit the committer match."""
    source_id = "auto-02322b616e34"
    lander_id = "auto-3f33c027a588"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "src"],
        check=True,
        capture_output=True,
    )
    source_sha = _commit(tmp_path, "land.py", dispatch_id=source_id)
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "master"],
        check=True,
        capture_output=True,
    )
    land_sha = _cherrypick_as_lander(
        tmp_path, source_sha=source_sha, lander_id=lander_id
    )
    rows = enumerate_tip_window_commits(
        tmp_path, admit_head=admit, closeout_head=land_sha
    )
    authored, unfiltered = partition_tip_window_meters(rows, dispatch_id=lander_id)
    assert authored == []
    assert unfiltered == [land_sha]
    assert observed_lane_git_refs(
        tmp_path,
        dispatch_id=lander_id,
        admit_head=admit,
        closeout_head=land_sha,
    ) == [land_sha]


def test_with_head_sha_fallback_unions_when_refs_empty_and_ahead() -> None:
    """T4/7414 shape — peer advance leaves refs=[] while commits_ahead>=1."""
    assert with_head_sha_fallback([], head_sha="deadbeef", commits_ahead=1) == [
        "deadbeef"
    ]


def test_with_head_sha_fallback_noop_when_commits_ahead_zero() -> None:
    """AC-P0 — measured zero must not manufacture a ref out of head_sha."""
    assert with_head_sha_fallback([], head_sha="deadbeef", commits_ahead=0) == []


def test_with_head_sha_fallback_noop_when_commits_ahead_absent() -> None:
    """AC-A1 — key-omitted commits_ahead must not manufacture a ref."""
    assert with_head_sha_fallback([], head_sha="deadbeef", commits_ahead=None) == []


def test_with_head_sha_fallback_noop_when_head_sha_missing() -> None:
    assert with_head_sha_fallback(["existing"], head_sha=None, commits_ahead=3) == [
        "existing"
    ]


def test_with_head_sha_fallback_unions_not_replaces_authored_refs() -> None:
    """AC — union, not replace: an already-observed lane ref is preserved."""
    refs = with_head_sha_fallback(
        ["authored-sha"], head_sha="peer-tip-sha", commits_ahead=2
    )
    assert refs == ["authored-sha", "peer-tip-sha"]


def test_with_head_sha_fallback_dedupes_when_head_sha_already_present() -> None:
    refs = with_head_sha_fallback(["deadbeef"], head_sha="deadbeef", commits_ahead=1)
    assert refs == ["deadbeef"]
