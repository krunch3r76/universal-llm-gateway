"""Git HEAD / lane commit path helpers (6341 own-commit attribution)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_home import dispatch_git_identity
from services.git_integration_worker.cursor_sdk_git_head import (
    observed_lane_git_refs,
    paths_exclusive_to_lane,
    paths_in_commit,
)

pytestmark = pytest.mark.offline


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
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
