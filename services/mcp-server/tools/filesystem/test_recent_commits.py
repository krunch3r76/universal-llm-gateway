"""fs(op=recent_commits) handler — .git refuse + hermetic repo resolve."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.filesystem._recent_commits import (
    path_touches_git_dir,
    recent_commits_impl,
)


def test_path_touches_git_dir() -> None:
    assert path_touches_git_dir("universal-llm-gateway/.git/HEAD") is True
    assert path_touches_git_dir("universal-llm-gateway/.git") is True
    assert path_touches_git_dir("universal-llm-gateway/libs/foo.py") is False


def test_recent_commits_refuses_git_dir() -> None:
    result = recent_commits_impl(path="universal-llm-gateway/.git/HEAD")
    assert "error" in result
    assert ".git" in result["error"]


def test_recent_commits_on_tmp_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "universal-llm-gateway"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True
    )
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )

    monkeypatch.setattr("tools._project_paths._PROJECT_ROOT", tmp_path)
    result = recent_commits_impl(path="universal-llm-gateway")
    assert "error" not in result, result
    assert result["head"]
    assert result["commits"][0]["subject"] == "init"
    assert "diff" not in result
