"""Tests for project-ask prompt-file preflight (24951)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_bundles.project_ask_prompt_files import (
    load_prompt_files,
    project_root_base,
    resolve_prompt_path,
)


def test_resolve_repo_relative_under_base(tmp_path: Path) -> None:
    prompt = tmp_path / "tmp" / "reviews" / "sealed.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("hello\n", encoding="utf-8")
    resolved = resolve_prompt_path("tmp/reviews/sealed.md", base=tmp_path)
    assert resolved == prompt.resolve()


def test_reject_relative_escape(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        resolve_prompt_path("../../etc/passwd", base=tmp_path)
    assert exc.value.code == 2


def test_load_missing_file_exits_before_register(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        load_prompt_files(["missing.md"], base=tmp_path)
    assert exc.value.code == 2


def test_load_multi_file_all_or_nothing(tmp_path: Path) -> None:
    good = tmp_path / "a.md"
    good.write_text("a", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_prompt_files(["a.md", "missing.md"], base=tmp_path)


def test_project_root_fallback_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    prompt = tmp_path / "tmp" / "reviews" / "x.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("x", encoding="utf-8")
    with patch(
        "claude_bundles.project_ask_prompt_files._REPO",
        tmp_path,
    ):
        loaded = load_prompt_files(["tmp/reviews/x.md"])
    assert loaded == ["x"]


def test_project_root_env_overrides_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "checkout"
    other.mkdir()
    prompt = other / "sealed.md"
    prompt.write_text("ok", encoding="utf-8")
    monkeypatch.setenv("PROJECT_ROOT", str(other))
    assert project_root_base() == other.resolve()
    assert load_prompt_files(["sealed.md"]) == ["ok"]
