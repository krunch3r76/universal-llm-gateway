"""Default cap for unwindowed text reads (offset=0, limit=0)."""

from __future__ import annotations

from pathlib import Path

from tools._file_helpers import read_file_result
from tools._line_range import DEFAULT_UNWINDOWED_MAX_BYTES, DEFAULT_UNWINDOWED_MAX_LINES


def test_large_unwindowed_read_is_capped(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    lines = [f"line-{i}" for i in range(DEFAULT_UNWINDOWED_MAX_LINES + 50)]
    (root / "big.txt").write_text("\n".join(lines), encoding="utf-8")
    result = read_file_result("big.txt", root=root)
    assert result["truncated"] is True
    assert result["total_lines"] == len(lines)
    assert result["line_range"]["returned"] <= DEFAULT_UNWINDOWED_MAX_LINES
    assert result["next_offset"] == result["line_range"]["returned"]
    assert len(result["content"].encode("utf-8")) <= DEFAULT_UNWINDOWED_MAX_BYTES


def test_small_unwindowed_read_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / "small.txt").write_text("a\nb\nc", encoding="utf-8")
    result = read_file_result("small.txt", root=root)
    assert result["content"] == "a\nb\nc"
    assert "truncated" not in result
    assert "line_range" not in result


def test_explicit_limit_path_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    lines = [f"line-{i}" for i in range(20)]
    (root / "mid.txt").write_text("\n".join(lines), encoding="utf-8")
    result = read_file_result("mid.txt", root=root, offset=0, limit=5)
    assert result["line_range"]["returned"] == 5
    assert result["truncated"] is True
    assert "next_offset" not in result
