"""Line-range read tests for read_file_result and apply_line_range."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools._file_helpers import read_file_result
from tools._line_range import apply_line_range


@pytest.fixture
def text_root(tmp_path: Path) -> Path:
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / "sample.txt").write_text(
        "line0\nline1\nline2\nline3\nline4", encoding="utf-8"
    )
    return root


def test_whole_file_unchanged_no_range_keys(text_root: Path) -> None:
    result = read_file_result("sample.txt", root=text_root)
    assert "line_range" not in result
    assert "total_lines" not in result
    assert "truncated" not in result
    assert result["content"] == "line0\nline1\nline2\nline3\nline4"


def test_offset_only(text_root: Path) -> None:
    result = read_file_result("sample.txt", root=text_root, offset=2, limit=0)
    assert result["content"] == "line2\nline3\nline4"
    assert result["line_range"] == {"offset": 2, "limit": 0, "returned": 3}
    assert result["total_lines"] == 5
    assert result["truncated"] is False


def test_limit_only(text_root: Path) -> None:
    result = read_file_result("sample.txt", root=text_root, offset=0, limit=2)
    assert result["content"] == "line0\nline1"
    assert result["line_range"]["returned"] == 2
    assert result["truncated"] is True


def test_offset_and_limit_exact_window(text_root: Path) -> None:
    result = read_file_result("sample.txt", root=text_root, offset=1, limit=2)
    assert result["content"] == "line1\nline2"
    assert result["line_range"] == {"offset": 1, "limit": 2, "returned": 2}
    assert result["total_lines"] == 5
    assert result["truncated"] is True


def test_eof_clamp_truncated_false(text_root: Path) -> None:
    result = read_file_result("sample.txt", root=text_root, offset=3, limit=10)
    assert result["content"] == "line3\nline4"
    assert result["line_range"]["returned"] == 2
    assert result["truncated"] is False


def test_offset_past_eof_empty_truncated_false(text_root: Path) -> None:
    result = read_file_result("sample.txt", root=text_root, offset=10, limit=5)
    assert result["content"] == ""
    assert result["line_range"]["returned"] == 0
    assert result["total_lines"] == 5
    assert result["truncated"] is False


@pytest.mark.parametrize("offset,limit", [(-1, 0), (0, -1), (-2, -3)])
def test_negative_offset_or_limit_raises(
    text_root: Path, offset: int, limit: int
) -> None:
    with pytest.raises(ValueError):
        read_file_result("sample.txt", root=text_root, offset=offset, limit=limit)


def test_binary_with_range_ignores_slice(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    png_bytes = b"\x89PNG\r\n\x1a\nfake"
    (root / "x.png").write_bytes(png_bytes)
    result = read_file_result("x.png", root=root, offset=1, limit=1)
    assert result["line_range_applied"] is False
    assert "line_range" not in result
    assert result["is_binary"] is True
    assert result["bytes"] == len(png_bytes)


def test_apply_line_range_unit() -> None:
    text = "a\nb\nc\nd"
    content, meta = apply_line_range(text, offset=1, limit=2)
    assert content == "b\nc"
    assert meta["line_range"] == {"offset": 1, "limit": 2, "returned": 2}
    assert meta["total_lines"] == 4
    assert meta["truncated"] is True


def test_project_root_parity_via_read_file_result(tmp_path: Path) -> None:
    """Same reader path as read_project_file — only root differs."""
    root = tmp_path / "project" / "universal-llm-gateway"
    root.mkdir(parents=True)
    rel = "config/mcp/canonical.yaml"
    lines = [f"line-{i}" for i in range(10)]
    (root / "config" / "mcp").mkdir(parents=True)
    (root / rel).write_text("\n".join(lines), encoding="utf-8")
    result = read_file_result(
        f"universal-llm-gateway/{rel}",
        root=tmp_path / "project",
        offset=2,
        limit=3,
    )
    assert result["content"] == "line-2\nline-3\nline-4"
    assert result["total_lines"] == 10
    assert result["truncated"] is True
