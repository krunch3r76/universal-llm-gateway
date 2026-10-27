"""Default cap for unwindowed text reads (offset=0, limit=0)."""

from __future__ import annotations

from pathlib import Path

from tools._file_helpers import read_file_result
from tools._line_range import DEFAULT_UNWINDOWED_MAX_BYTES, DEFAULT_UNWINDOWED_MAX_LINES


def test_large_unwindowed_plain_read_is_addressable(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    lines = [f"line-{i}" for i in range(DEFAULT_UNWINDOWED_MAX_LINES + 50)]
    (root / "big.txt").write_text("\n".join(lines), encoding="utf-8")
    result = read_file_result("big.txt", root=root)
    assert result["unwindowed_oversized"] is True
    assert result["kind"] == "line_window"
    assert result["total_lines"] == len(lines)
    assert result["truncated"] is True
    assert result["next_read"].startswith('fs(op="read", path="big.txt"')
    assert len(result["content"].encode("utf-8")) <= DEFAULT_UNWINDOWED_MAX_BYTES


def test_small_unwindowed_read_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / "small.txt").write_text("a\nb\nc", encoding="utf-8")
    result = read_file_result("small.txt", root=root)
    assert result["content"] == "a\nb\nc"
    assert "truncated" not in result
    assert "line_range" not in result
    assert "unwindowed_oversized" not in result


def test_explicit_limit_path_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    lines = [f"line-{i}" for i in range(20)]
    (root / "mid.txt").write_text("\n".join(lines), encoding="utf-8")
    result = read_file_result("mid.txt", root=root, offset=0, limit=5)
    assert result["line_range"]["returned"] == 5
    assert result["truncated"] is True
    assert "next_offset" not in result


def test_large_unwindowed_markdown_lists_sections(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    body = "\n\n".join(
        [
            "# Doc",
            "## Executive envelope\n" + ("summary line\n" * 1200),
            "## effects_manifest\n" + ("effect\n" * 1200),
            "## closeout_body\n" + ("detail\n" * 1200),
            "padding\n" * 50,
        ]
    )
    assert len(body.encode("utf-8")) > DEFAULT_UNWINDOWED_MAX_BYTES
    rel = "tmp/reviews/closeouts/dispatch-1.md"
    (root / "tmp" / "reviews" / "closeouts").mkdir(parents=True)
    (root / rel).write_text(body, encoding="utf-8")

    result = read_file_result(rel, root=root)
    assert result["unwindowed_oversized"] is True
    assert result["kind"] == "markdown_sections"
    assert result["truncated"] is True
    assert result.get("first_section_truncated") is True
    assert len(result["sections"]) >= 3
    assert result["first_section"]["heading"] in {"Doc", "[Preamble]"}
    assert result["section_reads"]
    assert all("md_read" in call for call in result["section_reads"])
    assert "effects_manifest" in result["section_reads"][0] or any(
        "effects_manifest" in c for c in result["section_reads"]
    )


def test_minified_json_single_line_gets_next_read(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    blob = "{" + ("x" * (DEFAULT_UNWINDOWED_MAX_BYTES + 4096)) + "}"
    (root / "data.json").write_text(blob, encoding="utf-8")
    result = read_file_result("data.json", root=root)
    assert result["kind"] == "line_window"
    assert result.get("byte_truncated") is True
    assert "next_read" in result
    assert result["truncated"] is True


def test_python_with_hash_comment_uses_line_window(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    lines = ["# comment\n" + ("pass\n" * 900)]
    (root / "script.py").write_text("".join(lines), encoding="utf-8")
    result = read_file_result("script.py", root=root)
    assert result["kind"] == "line_window"
    assert "markdown_sections" != result.get("kind")


def _reassemble_line_windows(root: Path, rel: str) -> bytes:
    chunks: list[str] = []
    result = read_file_result(rel, root=root)
    chunks.append(result.get("content") or "")
    offset = result.get("next_offset")
    while result.get("next_read") and offset is not None:
        result = read_file_result(
            rel, root=root, offset=int(offset), limit=DEFAULT_UNWINDOWED_MAX_LINES
        )
        chunks.append(result.get("content") or "")
        if not result.get("truncated", False):
            break
        lr = result.get("line_range") or {}
        offset = int(lr.get("offset", 0)) + int(lr.get("returned", 0))
    return "".join(chunks).encode("utf-8")


def test_multiline_byte_cap_ends_on_line_boundary_and_reassembles(
    tmp_path: Path,
) -> None:
    """>32 KB and <800 lines: no silent mid-line cut; windows reassemble to source."""
    root = tmp_path / "sandbox"
    root.mkdir()
    line_payload = "x" * 700
    lines = [f"row-{i}-{line_payload}" for i in range(60)]
    rel = "wide.txt"
    (root / rel).write_text("\n".join(lines), encoding="utf-8")
    source_bytes = (root / rel).read_bytes()
    assert len(source_bytes) > DEFAULT_UNWINDOWED_MAX_BYTES
    assert len(lines) < DEFAULT_UNWINDOWED_MAX_LINES

    first = read_file_result(rel, root=root)
    assert first["kind"] == "line_window"
    assert first["truncated"] is True
    assert "byte_truncated" not in first or first.get("byte_truncated") is not True
    content = first["content"]
    assert content
    assert content.endswith("\n") or "\n" not in content[content.rfind("\n") + 1 :]

    assert _reassemble_line_windows(root, rel) == source_bytes


def test_under_threshold_markdown_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    text = "## One\nhello\n"
    (root / "tiny.md").write_text(text, encoding="utf-8")
    result = read_file_result("tiny.md", root=root)
    assert result["content"] == text
    assert "unwindowed_oversized" not in result
