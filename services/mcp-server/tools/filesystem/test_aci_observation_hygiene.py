"""ACI observation-hygiene runtime tests (fs list/search/read empty-success)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools._file_helpers import read_file_result
from tools.filesystem._ops_search import search_file_impl
from tools.filesystem._ops_text import list_files_impl


@pytest.fixture
def sandbox_root(tmp_path: Path) -> Path:
    root = tmp_path / "sandbox"
    root.mkdir()
    return root


def test_list_files_missing_path_observation(sandbox_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.filesystem._ops_text.SANDBOX_ROOT",
        sandbox_root,
    )
    monkeypatch.setattr(
        "tools.filesystem._ops_text.safe_path",
        lambda p: sandbox_root / p if p else sandbox_root,
    )
    result = list_files_impl("missing-dir")
    assert result["files"] == []
    assert result["status"] == "path_not_found"
    assert "observation" in result
    assert "does not exist" in result["observation"]


def test_list_files_empty_directory_observation(sandbox_root: Path, monkeypatch) -> None:
    empty = sandbox_root / "empty"
    empty.mkdir()
    monkeypatch.setattr(
        "tools.filesystem._ops_text.SANDBOX_ROOT",
        sandbox_root,
    )
    monkeypatch.setattr(
        "tools.filesystem._ops_text.safe_path",
        lambda p: sandbox_root / p if p else sandbox_root,
    )
    result = list_files_impl("empty")
    assert result["status"] == "empty_directory"
    assert result["observation"]


def test_search_file_no_matches_observation(sandbox_root: Path, monkeypatch) -> None:
    sample = sandbox_root / "a.txt"
    sample.write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.filesystem._ops_search.safe_path",
        lambda p: sandbox_root / p,
    )
    result = search_file_impl("a.txt", "ZZZ_NOMATCH")
    assert result["matches"] == []
    assert result["status"] == "no_matches"
    assert "observation" in result


def test_read_file_empty_window_observation(sandbox_root: Path) -> None:
    sample = sandbox_root / "sample.txt"
    sample.write_text("line0\nline1\n", encoding="utf-8")
    result = read_file_result("sample.txt", root=sandbox_root, offset=10, limit=5)
    assert result["content"] == ""
    assert "observation" in result
    assert "zero lines" in result["observation"]
