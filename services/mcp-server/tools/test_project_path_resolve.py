"""Tests for workspaces path resolution (agent-bus thread 1190)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import _project_paths as paths
from tools.project import _safe_project_path


def test_normalize_directory_dot() -> None:
    assert paths.normalize_directory_arg(".") == ""
    assert paths.normalize_directory_arg("./") == ""


def test_resolve_repo_relative_path_multi_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    target = repo / "libs" / "cortex_store" / "routes" / "session_handoff.py"
    target.parent.mkdir(parents=True)
    target.write_text("# stub\n", encoding="utf-8")

    resolved = paths.resolve_existing_file(
        "libs/cortex_store/routes/session_handoff.py",
        root=tmp_path,
    )
    assert resolved == target.resolve()


def test_resolve_prefixed_path(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    target = repo / "routes" / "foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("x\n", encoding="utf-8")

    resolved = paths.resolve_existing_file(
        "universal-llm-gateway/routes/foo.py",
        root=tmp_path,
    )
    assert resolved == target.resolve()


def test_multi_repo_root_unscoped(tmp_path: Path) -> None:
    (tmp_path / "universal-llm-gateway").mkdir()
    (tmp_path / "other-repo").mkdir()
    assert paths.multi_repo_root_unscoped(tmp_path) is True
    assert paths.multi_repo_root_unscoped(tmp_path / "universal-llm-gateway") is False


def test_safe_project_path_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(root))
    monkeypatch.setattr("tools.project._PROJECT_ROOT", root.resolve())
    with pytest.raises(ValueError, match="traversal rejected"):
        _safe_project_path("../../etc/passwd")
