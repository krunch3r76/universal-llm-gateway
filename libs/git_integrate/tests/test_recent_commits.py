"""Hermetic tests for oneline recent-commits helper (no diffs)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_integrate.recent_commits import (
    MAX_N,
    format_hop_slice,
    log_oneline,
)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _repo_with_commits(tmp_path: Path, n: int) -> tuple[Path, list[str]]:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    shas: list[str] = []
    for i in range(n):
        (repo / "f.txt").write_text(f"{i}\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-m", f"commit-{i}")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    return repo, shas


def test_oneline_newest_first_oldest_last(tmp_path: Path) -> None:
    repo, shas = _repo_with_commits(tmp_path, 3)
    result = log_oneline(repo, n=15)
    assert result["head"] == shas[-1]
    assert [c["sha"] for c in result["commits"]] == list(reversed(shas))
    assert [c["subject"] for c in result["commits"]] == [
        "commit-2",
        "commit-1",
        "commit-0",
    ]
    assert result["since"] == "last 15"
    assert result["truncated"] is False
    for commit in result["commits"]:
        assert set(commit) == {"sha", "subject", "author", "authored_at"}


def test_since_sha_excludes_bound(tmp_path: Path) -> None:
    repo, shas = _repo_with_commits(tmp_path, 5)
    result = log_oneline(repo, since=shas[1], n=15)
    assert [c["sha"] for c in result["commits"]] == list(reversed(shas[2:]))
    assert result["since"] == shas[1]
    assert result["truncated"] is False


def test_cap_and_truncated(tmp_path: Path) -> None:
    repo, _shas = _repo_with_commits(tmp_path, 25)
    result = log_oneline(repo, n=15)
    assert len(result["commits"]) == 15
    assert result["truncated"] is True
    clamped = log_oneline(repo, n=99)
    assert len(clamped["commits"]) == MAX_N
    assert clamped["truncated"] is True


def test_no_diffs_in_payload(tmp_path: Path) -> None:
    repo, _shas = _repo_with_commits(tmp_path, 2)
    result = log_oneline(repo)
    blob = str(result)
    assert "diff --git" not in blob
    assert "@@" not in blob
    assert "patch" not in result
    assert "diff" not in result
    for commit in result["commits"]:
        assert "diff" not in commit
        assert "patch" not in commit
        assert "files" not in commit


def test_invalid_since_rejected(tmp_path: Path) -> None:
    repo, _shas = _repo_with_commits(tmp_path, 1)
    with pytest.raises(ValueError, match="since must be a git SHA"):
        log_oneline(repo, since="master")
    with pytest.raises(ValueError, match="since must be a git SHA"):
        log_oneline(repo, since="HEAD")


def test_hop_slice_keeps_query_pointer(tmp_path: Path) -> None:
    repo, shas = _repo_with_commits(tmp_path, 2)
    result = log_oneline(repo, n=8)
    body = format_hop_slice(result)
    assert f"recent_commits: HEAD={shas[-1]}" in body
    assert f'since="{shas[-1]}"' in body
    assert "query: fs(op=\"recent_commits\"" in body
    dropped = format_hop_slice(result, include_body=False)
    assert "body dropped for screen budget" in dropped
    assert "commit-1" not in dropped
    assert "query: fs(op=\"recent_commits\"" in dropped
