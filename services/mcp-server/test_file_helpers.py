"""Regression tests for MCP file-helper PDF behavior."""

from __future__ import annotations

import time
from pathlib import Path

import pytest


def test_read_pdf_prefers_readable_sidecar(tmp_path: Path) -> None:
    from tools._file_helpers import read_file_result

    pdf_path = tmp_path / "case-law" / "example.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(b"%PDF-1.7\n")
    sidecar = pdf_path.with_name("example-readable.md")
    sidecar.write_text("# Example\n\nExtracted case text.", encoding="utf-8")

    result = read_file_result("case-law/example.pdf", root=tmp_path)

    assert result["content"] == "# Example\n\nExtracted case text."
    assert result["extraction_method"] == "sidecar_markdown"
    assert result["sidecar_path"] == str(sidecar)


def test_read_pdf_timeout_emits_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import _file_helpers

    events: list[tuple[str, dict[str, object]]] = []

    def _slow_extract(_path: Path) -> str:
        time.sleep(0.05)
        return "late"

    monkeypatch.setattr(_file_helpers, "_extract_pdf_markdown", _slow_extract)
    monkeypatch.setattr(
        _file_helpers,
        "record",
        lambda signal, **payload: events.append((signal, payload)),
    )

    with pytest.raises(TimeoutError):
        _file_helpers._read_pdf(tmp_path / "slow.pdf", timeout_s=0.001)

    assert events
    assert events[0][0] == "mcp.tool.file.read.timeout"
    assert events[0][1]["extension"] == ".pdf"
