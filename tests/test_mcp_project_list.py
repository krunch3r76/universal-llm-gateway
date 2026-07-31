from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path


def _load_project_module():
    service_root = (
        Path(__file__).resolve().parents[1] / "services" / "mcp-server"
    )
    service_root_str = str(service_root)
    if service_root_str not in sys.path:
        sys.path.insert(0, service_root_str)
    sys.modules.pop("tools.project", None)
    return importlib.import_module("tools.project")


def _get_list_project_files(project_module):
    listing = None

    class _DummyMcp:
        def tool(self):
            def decorator(fn):
                nonlocal listing
                if fn.__name__ == "list_project_files":
                    listing = fn
                return fn

            return decorator

    project_module.register_project_tools(_DummyMcp())
    assert listing is not None
    return listing


def test_list_project_files_surfaces_empty_untracked_directories(tmp_path, monkeypatch):
    project = _load_project_module()
    monkeypatch.setattr(project, "_PROJECT_ROOT", tmp_path)

    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "docs" / "overview.md").write_text("hello", encoding="utf-8")

    listing = _get_list_project_files(project)

    result = listing(max_depth=3, include_untracked=True)

    assert result["files"] == ["docs/overview.md"]
    assert result["directories"] == ["docs", "docs/architecture"]
    assert result["truncated"] is False


def test_list_project_files_tracked_mode_respects_depth_and_hides_untracked(
    tmp_path, monkeypatch
):
    project = _load_project_module()
    monkeypatch.setattr(project, "_PROJECT_ROOT", tmp_path)

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "tracked.txt").write_text("tracked", encoding="utf-8")
    (tmp_path / "pkg" / "untracked.txt").write_text("untracked", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "pkg/tracked.txt"], cwd=tmp_path, check=True)

    listing = _get_list_project_files(project)

    result = listing(max_depth=1, include_untracked=False)

    assert result["files"] == []
    assert result["directories"] == ["pkg"]
    assert result["truncated"] is False
