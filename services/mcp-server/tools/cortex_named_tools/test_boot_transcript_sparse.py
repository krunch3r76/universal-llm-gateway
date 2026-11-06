"""Boot transcript must not silently accept manifest-only source reads."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tools._file_helpers import read_file_result as _read_file_result
from tools.cortex_named_tools._boot_transcript import resolve_transcript


@pytest.mark.offline
def test_transcript_export_read_returns_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md = tmp_path / "t.md"
    md.write_text("# Transcript\n\nhello boot\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.cortex_named_tools._boot_transcript.read_file_result",
        lambda path, **kw: _read_file_result(path, root=tmp_path, **kw),
    )

    def _cx(method: str, path: str) -> dict:
        assert method == "GET"
        if path.startswith("/entities/"):
            return {"source_uri": "t.md", "id": "transcript:abc"}
        return {"items": []}

    with patch("tools.cortex_named_tools._boot_transcript.cx", side_effect=_cx):
        out = resolve_transcript("abc")

    assert "error" not in out
    assert "hello boot" in out.get("markdown", "")


@pytest.mark.offline
def test_transcript_empty_after_read_is_fail_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "empty.md").write_text("   \n\t\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.cortex_named_tools._boot_transcript.read_file_result",
        lambda path, **kw: _read_file_result(path, root=tmp_path, **kw),
    )

    def _cx(method: str, path: str) -> dict:
        if path.startswith("/entities/"):
            return {"source_uri": "empty.md", "id": "transcript:empty"}
        return {"items": []}

    with patch("tools.cortex_named_tools._boot_transcript.cx", side_effect=_cx):
        out = resolve_transcript("empty")

    assert out.get("error") == "transcript_source_empty"


@pytest.mark.offline
def test_transcript_export_stitches_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Truncated export with an int cursor is followed, not clipped."""
    calls: list[dict] = []

    def _read(path: str, **kwargs: object) -> dict:
        calls.append(dict(kwargs))
        cursor = kwargs.get("chunk_cursor")
        if cursor is None:
            return {"content": "AAA", "truncated": True, "chunk_cursor_next": 3}
        assert cursor == 3
        return {"content": "BBB", "truncated": False}

    monkeypatch.setattr(
        "tools.cortex_named_tools._boot_transcript.read_file_result",
        _read,
    )

    def _cx(method: str, path: str) -> dict:
        if path.startswith("/entities/"):
            return {"source_uri": "t.md", "id": "transcript:chunked"}
        return {"items": []}

    with patch("tools.cortex_named_tools._boot_transcript.cx", side_effect=_cx):
        out = resolve_transcript("chunked")

    assert out.get("error") is None
    assert "AAABBB" in out.get("markdown", "")
    assert calls[0].get("export") is True
    assert calls[1].get("chunk_cursor") == 3


@pytest.mark.offline
def test_transcript_truncated_without_cursor_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _read(path: str, **kwargs: object) -> dict:
        return {"content": "AAA", "truncated": True}

    monkeypatch.setattr(
        "tools.cortex_named_tools._boot_transcript.read_file_result",
        _read,
    )

    def _cx(method: str, path: str) -> dict:
        if path.startswith("/entities/"):
            return {"source_uri": "t.md", "id": "transcript:clip"}
        return {"items": []}

    with patch("tools.cortex_named_tools._boot_transcript.cx", side_effect=_cx):
        out = resolve_transcript("clip")

    assert out.get("error") == "transcript_export_truncated"
