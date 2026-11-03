"""fs(op=recent_commits) handler — .git refuse + hermetic repo resolve."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.filesystem._recent_commits import (
    path_touches_git_dir,
    recent_commits_impl,
    resolve_path_scope,
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
    assert result["path_scope"] is None
    assert "diff" not in result


def _init_repo(repo: Path) -> None:
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


def test_recent_commits_path_scope_filters_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "universal-llm-gateway"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "touch-a"], cwd=repo, check=True, capture_output=True
    )
    (repo / "b.txt").write_text("b\n")
    subprocess.run(["git", "add", "b.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "touch-b"], cwd=repo, check=True, capture_output=True
    )

    monkeypatch.setattr("tools._project_paths._PROJECT_ROOT", tmp_path)
    root_result = recent_commits_impl(path="universal-llm-gateway")
    scoped = recent_commits_impl(path="universal-llm-gateway/a.txt")
    assert root_result["path_scope"] is None
    assert len(root_result["commits"]) == 2
    assert scoped["path_scope"] == "a.txt"
    assert [c["subject"] for c in scoped["commits"]] == ["touch-a"]

    missing = recent_commits_impl(path="universal-llm-gateway/no/such/file")
    assert missing["path_scope"] == "no/such/file"
    assert missing["commits"] == []


def test_resolve_path_scope_repo_root() -> None:
    root = Path("/data/project")
    repo = root / "universal-llm-gateway"
    scope, err = resolve_path_scope("universal-llm-gateway", repo)
    assert scope is None
    assert err is None
