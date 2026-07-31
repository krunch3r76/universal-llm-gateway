"""Regression tests for extract_document helpers (frictions 24427 / 24428)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("pymupdf")
import pymupdf  # type: ignore[import-untyped]


def _make_mixed_pdf(path: Path) -> None:
    """Page 1 = text-rich; page 2 = thin text + embedded image (OCR candidate)."""
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text(
        (72, 72),
        "This is a substantial text layer with enough alphanumeric content "
        "to clear the thin-page threshold for digital extraction.",
    )
    p2 = doc.new_page()
    # Minimal PNG so the page embeds an image; almost no extractable text.
    import struct
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 8 for _ in range(8))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    img_path = path.with_suffix(".png")
    img_path.write_bytes(png)
    p2.insert_image(pymupdf.Rect(72, 72, 200, 200), filename=str(img_path))
    p2.insert_text((72, 40), "x")  # thin (1 alnum)
    doc.save(str(path))
    doc.close()


def test_extract_text_text_pdf_returns_str_not_tuple(tmp_path: Path) -> None:
    """Friction 24427: unpack read_pdf; never nest (text, method)."""
    from tools._extract_document_helpers import extract_text

    pdf = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello extract_document regression")
    doc.save(str(pdf))
    doc.close()

    text, tokens = extract_text(
        pdf,
        "text_pdf",
        dpi=200,
        model="",
        prompt="",
        pages=None,
    )
    assert isinstance(text, str)
    assert tokens is None
    assert "hello extract_document" in text
    # Caller contract: strip must not raise.
    assert text.strip()


def test_page_gate_routes_image_locked_page_to_ocr(tmp_path: Path) -> None:
    """Friction 24428: pages=[image_page] → ocr_pages even when PDF has text layer."""
    from tools._extract_document_helpers import extract_text
    from tools._extract_document_page_gate import classify_selected_pages

    pdf = tmp_path / "mixed.pdf"
    _make_mixed_pdf(pdf)

    ocr_list, text_by_page = classify_selected_pages(pdf, [1, 2])
    assert 1 in text_by_page
    assert ocr_list == [2]

    fake_ocr = {
        "text": "\n--- Page 2 ---\nOCR RECOVERED ANNOTATION TEXT",
        "total_tokens": 42,
        "pages": 1,
        "per_page": [
            {"page": 2, "text": "OCR RECOVERED ANNOTATION TEXT", "tokens_used": 42}
        ],
        "model": "test/vision",
    }
    with patch(
        "tools._extract_document_page_gate.ocr_pages",
        return_value=fake_ocr,
    ) as mock_ocr:
        text, tokens = extract_text(
            pdf,
            "text_pdf",
            dpi=200,
            model="test/vision",
            prompt="extract",
            pages=[2],
        )
        mock_ocr.assert_called_once()
        assert mock_ocr.call_args.kwargs.get("pages") == [2]
        assert "OCR RECOVERED ANNOTATION TEXT" in text
        assert tokens == 42


def test_page_gate_text_rich_selected_pages_skip_ocr(tmp_path: Path) -> None:
    """With pages= on text-rich pages only, stay on pymupdf (no ocr_pages)."""
    from tools._extract_document_helpers import extract_text

    pdf = tmp_path / "mixed.pdf"
    _make_mixed_pdf(pdf)

    with patch(
        "tools._extract_document_page_gate.ocr_pages",
    ) as mock_ocr:
        text, tokens = extract_text(
            pdf,
            "text_pdf",
            dpi=200,
            model="",
            prompt="",
            pages=[1],
        )
        mock_ocr.assert_not_called()
        assert tokens is None
        assert "substantial text layer" in text


def test_full_document_text_pdf_unchanged_no_page_filter(tmp_path: Path) -> None:
    """pages=None keeps the read_pdf full-document path (no page-gate OCR)."""
    from tools._extract_document_helpers import extract_text

    pdf = tmp_path / "mixed.pdf"
    _make_mixed_pdf(pdf)

    with patch(
        "tools._extract_document_page_gate.ocr_pages",
    ) as mock_ocr:
        text, tokens = extract_text(
            pdf,
            "text_pdf",
            dpi=200,
            model="",
            prompt="",
            pages=None,
        )
        mock_ocr.assert_not_called()
        assert isinstance(text, str)
        assert tokens is None
        assert "substantial text layer" in text
