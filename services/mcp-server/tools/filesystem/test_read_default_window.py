"""AC2–4: default plain-source read window (400 lines OR 48 KiB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools._file_helpers import read_file_result
from tools._line_range import DEFAULT_UNWINDOWED_MAX_BYTES, DEFAULT_UNWINDOWED_MAX_LINES


def test_over_threshold_plain_read_returns_window_fields(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    lines = [f"line-{i}" for i in range(DEFAULT_UNWINDOWED_MAX_LINES + 10)]
    (root / "big.txt").write_text("\n".join(lines), encoding="utf-8")
    result = read_file_result("big.txt", root=root)
    assert result["truncated"] is True
    assert result["total_lines"] == len(lines)
    assert "line_range" in result
    assert result["next_offset"] == int(result["line_range"]["returned"])
    assert len(result["content"].encode("utf-8")) <= DEFAULT_UNWINDOWED_MAX_BYTES


def test_under_threshold_returns_full_body(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    text = "alpha\nbeta\ngamma\n"
    (root / "small.txt").write_text(text, encoding="utf-8")
    result = read_file_result("small.txt", root=root)
    assert result["content"] == text
    assert "truncated" not in result
    assert "next_offset" not in result


def test_explicit_offset_limit_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    lines = [f"line-{i}" for i in range(30)]
    (root / "mid.txt").write_text("\n".join(lines), encoding="utf-8")
    result = read_file_result("mid.txt", root=root, offset=5, limit=3)
    assert result["line_range"]["offset"] == 5
    assert result["line_range"]["returned"] == 3
    assert "next_offset" not in result


def test_read_multi_each_file_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools._read_files_batch import read_files_batch

    root = tmp_path / "sandbox"
    root.mkdir()
    lines = [f"row-{i}" for i in range(DEFAULT_UNWINDOWED_MAX_LINES + 5)]
    for name in ("a.txt", "b.txt"):
        (root / name).write_text("\n".join(lines), encoding="utf-8")

    out = read_files_batch(["a.txt", "b.txt"], root=root)
    assert "files" in out
    files = out["files"]
    assert isinstance(files, dict)
    for item in files.values():
        assert item["truncated"] is True
        assert item["total_lines"] > DEFAULT_UNWINDOWED_MAX_LINES
