"""Regression tests for MCP file-helper PDF behavior."""

from __future__ import annotations

import time
from pathlib import Path

import pytest


def test_read_pdf_prefers_readable_sidecar(tmp_path: Path) -> None:
    from tools import _file_helpers

    pdf_path = tmp_path / "case-law" / "example.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(b"%PDF-1.7\n")
    sidecar = pdf_path.with_name("example-readable.md")
    sidecar.write_text("# Example\n\nExtracted case text.", encoding="utf-8")

    result = _file_helpers.read_file_result("case-law/example.pdf", root=tmp_path)

    assert result["content"] == "# Example\n\nExtracted case text."
    assert result["extraction_method"] == _file_helpers.PDF_METHOD_SIDECAR
    assert result["sidecar_path"] == str(sidecar)


def test_read_pdf_timeout_emits_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import _file_helpers

    events: list[tuple[str, dict[str, object]]] = []

    def _slow_extract(_path: Path) -> tuple[str, str]:
        time.sleep(0.05)
        return "late", _file_helpers.PDF_METHOD_LAYOUT

    def _fast_plaintext(_path: Path) -> str:
        return "plaintext fallback result"

    monkeypatch.setattr(_file_helpers, "_extract_pdf_markdown", _slow_extract)
    monkeypatch.setattr(_file_helpers, "_extract_pdf_plaintext", _fast_plaintext)
    monkeypatch.setattr(
        _file_helpers,
        "record",
        lambda signal, **payload: events.append((signal, payload)),
    )

    # File must exist on disk — _read_pdf now stats before constructing the executor.
    slow_pdf = tmp_path / "slow.pdf"
    slow_pdf.write_bytes(b"%PDF-1.7\n")

    text, method = _file_helpers._read_pdf(slow_pdf, timeout_s=0.001)
    assert text == "plaintext fallback result"
    assert method == _file_helpers.PDF_METHOD_PLAINTEXT_TIMEOUT

    # Two events: layout-timeout signal + unified plaintext-fallback signal
    # carrying a `cause` discriminator.
    timeout_events = [e for e in events if e[0] == "mcp.tool.pdf.read.timeout"]
    plaintext_events = [e for e in events if e[0] == "mcp.tool.pdf.read.plaintext"]
    assert len(timeout_events) == 1
    assert timeout_events[0][1]["extension"] == ".pdf"
    assert len(plaintext_events) == 1
    assert plaintext_events[0][1]["cause"] == "timeout_fallback"


def test_read_pdf_size_gate_emits_plaintext_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Files above PDF_LAYOUT_MAX_BYTES skip pymupdf4llm and emit cause=gated."""
    from tools import _file_helpers

    events: list[tuple[str, dict[str, object]]] = []

    def _fast_plaintext(_path: Path) -> str:
        return "plaintext-gated content"

    monkeypatch.setattr(_file_helpers, "_extract_pdf_plaintext", _fast_plaintext)
    monkeypatch.setattr(
        _file_helpers,
        "record",
        lambda signal, **payload: events.append((signal, payload)),
    )
    monkeypatch.setattr(_file_helpers, "PDF_LAYOUT_MAX_BYTES", 16)

    big_pdf = tmp_path / "big.pdf"
    big_pdf.write_bytes(b"%PDF-1.7\n" + (b"\x00" * 64))

    text, method = _file_helpers._read_pdf(big_pdf)
    assert text == "plaintext-gated content"
    assert method == _file_helpers.PDF_METHOD_PLAINTEXT_GATED

    plaintext_events = [e for e in events if e[0] == "mcp.tool.pdf.read.plaintext"]
    assert len(plaintext_events) == 1
    assert plaintext_events[0][1]["cause"] == "gated"
    assert plaintext_events[0][1]["threshold_bytes"] == 16
