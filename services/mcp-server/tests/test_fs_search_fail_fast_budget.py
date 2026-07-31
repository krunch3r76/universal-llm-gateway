"""Hermetic tests for fs search fail-fast wall budget + oversized native skip."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.filesystem import _ops_search as ops_search
from tools.filesystem import _paths as paths

from tools import _search_helpers


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    monkeypatch.setattr(ops_search, "SANDBOX_ROOT", root)
    monkeypatch.setattr(ops_search, "record", lambda *_a, **_k: None)
    return root


def test_directory_skips_oversized_native_without_read(
    sandbox_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = sandbox_root / "small.txt"
    small.write_text("needle here\n", encoding="utf-8")
    large = sandbox_root / "large.txt"
    large.write_bytes(b"x" * (_search_helpers.SEARCH_NATIVE_MAX_BYTES + 1))

    read_calls: list[Path] = []
    original_read = Path.read_text

    def _track_read(self: Path, *args: object, **kwargs: object) -> str:
        read_calls.append(self)
        return original_read(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _track_read)

    result = ops_search.search_directory_impl("", "needle")

    assert result["skipped_oversized"] >= 1
    assert read_calls == [small]
    assert any(m["file"] == "small.txt" for m in result["matches"])
    assert "_warning" in result
    assert "size cap" in result["_warning"]


def test_oversized_skip_emits_warning(sandbox_root: Path) -> None:
    (sandbox_root / "big.log").write_bytes(
        b"0" * (_search_helpers.SEARCH_NATIVE_MAX_BYTES + 100)
    )

    result = ops_search.search_directory_impl("", "missing")

    assert result["skipped_oversized"] >= 1
    assert "24276" in result.get("_warning", "")


def test_wall_budget_truncates_directory_search(
    sandbox_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for idx in range(20):
        (sandbox_root / f"file{idx:02d}.txt").write_text(
            f"line {idx}\n", encoding="utf-8"
        )

    start = 1000.0
    clock = {"now": start}

    def fake_monotonic() -> float:
        clock["now"] += 5.0
        return clock["now"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    monkeypatch.setattr(_search_helpers.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(_search_helpers, "SEARCH_WALL_BUDGET_S", 10.0)

    result = ops_search.search_directory_impl("", "line")

    assert result["truncated"] is True
    assert "wall budget" in result.get("_warning", "").lower()


def test_search_prunes_tmp_and_runtime_dirs(sandbox_root: Path) -> None:
    (sandbox_root / "ok.txt").write_text("visible needle\n", encoding="utf-8")
    tmp_dir = sandbox_root / "tmp"
    tmp_dir.mkdir()
    (tmp_dir / "hidden.txt").write_text("hidden needle\n", encoding="utf-8")
    runtime_dir = sandbox_root / ".runtime"
    runtime_dir.mkdir()
    (runtime_dir / "hidden2.txt").write_text("hidden needle\n", encoding="utf-8")

    result = ops_search.search_directory_impl("", "needle")

    files = {m["file"] for m in result["matches"]}
    assert "ok.txt" in files
    assert not any("tmp/" in f for f in files)
    assert not any(".runtime/" in f for f in files)


def test_file_mode_applies_size_cap(sandbox_root: Path) -> None:
    rel = "solo.txt"
    target = sandbox_root / rel
    target.write_bytes(b"z" * (_search_helpers.SEARCH_NATIVE_MAX_BYTES + 50))

    with patch.object(Path, "read_text", side_effect=AssertionError("must not read")):
        result = ops_search.search_file_impl(rel, "z")

    assert result["skipped_oversized"] >= 1
    assert result["matches"] == []
    assert "size cap" in result.get("_warning", "")


def test_workspaces_listing_respects_wall_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import project as project_mod

    root = tmp_path / "project"
    root.mkdir()
    for idx in range(30):
        (root / f"f{idx}.txt").write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(project_mod, "_PROJECT_ROOT", root)
    start = 500.0
    clock = {"now": start}

    def fake_monotonic() -> float:
        clock["now"] += 1.0
        return clock["now"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    monkeypatch.setattr(project_mod.time, "monotonic", fake_monotonic)
    deadline = start + 3.0

    files, _dirs, truncated = project_mod._filesystem_listing(
        "",
        skip_binary=False,
        cap=None,
        extra_skip_dirs=_search_helpers.SEARCH_SKIP_DIRS,
        deadline=deadline,
    )

    assert truncated is True
    assert len(files) < 30


def test_workspaces_git_tracked_enum_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import project as project_mod

    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(project_mod, "_PROJECT_ROOT", root)

    def fake_discover() -> list[Path]:
        return [root]

    def slow_ls(_repo: Path, _sub: str) -> list[str]:
        time.sleep(0)  # placeholder — deadline checked after return
        return [f"file{i}.txt" for i in range(50)]

    monkeypatch.setattr(project_mod, "_discover_repos", fake_discover)
    monkeypatch.setattr(project_mod, "_git_ls_files_in_repo", slow_ls)

    past_deadline = time.monotonic() - 1.0
    files, truncated = project_mod._git_tracked_files("", deadline=past_deadline)

    assert truncated is True
    assert files == []
