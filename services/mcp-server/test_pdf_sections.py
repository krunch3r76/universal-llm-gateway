"""Regression tests for outline-driven PDF section navigation.

Covers the fix for agent-bus thread 1341: heading-rich PDFs whose titles do
not convert to ATX markdown previously returned a single ``[Preamble]``. The
navigator now drives sections from the embedded outline (with a per-page
fallback when none exists).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest


def _make_pdf(tmp_path: Path, pages: int, *, toc: list[list] | None = None) -> Path:
    """Build a PDF; ``toc`` entries are ``[level, title, page1based]``.

    Each outline entry is given an explicit destination near the page top
    (``y=730`` in PDF space ≈ ``top_y=62``) so the navigator clips real text,
    mirroring how authored PDFs carry destination coordinates.
    """
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Heading {i + 1}")
        page.insert_text((72, 120), f"Body text of page {i + 1}. Lorem ipsum dolor.")
    if toc is not None:
        doc.set_toc(
            [
                [level, title, page, {"kind": 1, "to": pymupdf.Point(72, 730)}]
                for level, title, page in toc
            ]
        )
    out = tmp_path / "doc.pdf"
    doc.save(str(out))
    doc.close()
    return out


def test_outline_drives_sections(tmp_path: Path) -> None:
    from tools._pdf_sections import list_pdf_sections

    pdf = _make_pdf(
        tmp_path,
        3,
        toc=[[1, "Alpha", 1], [2, "Alpha Child", 2], [1, "Beta", 3]],
    )
    listing = list_pdf_sections(pdf)

    assert listing["source"] == "pdf_toc"
    sections = listing["sections"]
    assert [s["heading"] for s in sections] == ["Alpha", "Alpha Child", "Beta"]
    # Nesting: the level-2 entry hangs under its level-1 parent.
    assert sections[1]["path"] == "Alpha/Alpha Child"
    assert sections[2]["path"] == "Beta"
    assert all(s["boundary_precision"] == "coordinate" for s in sections)


def test_read_section_returns_bounded_text(tmp_path: Path) -> None:
    from tools._pdf_sections import read_pdf_section

    pdf = _make_pdf(tmp_path, 3, toc=[[1, "Alpha", 1], [1, "Beta", 3]])
    body = read_pdf_section(pdf, "Beta")

    assert "page 3" in body
    assert "page 1" not in body


def test_no_outline_falls_back_to_pages(tmp_path: Path) -> None:
    from tools._pdf_sections import list_pdf_sections, read_pdf_section

    pdf = _make_pdf(tmp_path, 2, toc=None)
    listing = list_pdf_sections(pdf)

    assert listing["source"] == "pdf_page_fallback"
    assert [s["heading"] for s in listing["sections"]] == ["Page 1", "Page 2"]
    assert all(s["boundary_precision"] == "page" for s in listing["sections"])
    assert "page 2" in read_pdf_section(pdf, "Page 2")


def test_to_dict_nests_by_outline(tmp_path: Path) -> None:
    from tools._pdf_sections import pdf_to_dict

    pdf = _make_pdf(
        tmp_path,
        3,
        toc=[[1, "Alpha", 1], [2, "Alpha Child", 2], [1, "Beta", 3]],
    )
    data = pdf_to_dict(pdf)

    assert set(data) == {"Alpha", "Beta"}
    # Alpha has a child, so it is a dict carrying its own body under _content.
    assert isinstance(data["Alpha"], dict)
    assert "Alpha Child" in data["Alpha"]
    assert "page 1" in data["Alpha"].get("_content", "")
    # Beta is a leaf — its value is the body string directly.
    assert isinstance(data["Beta"], str)
    assert "page 3" in data["Beta"]


def test_unknown_selector_raises(tmp_path: Path) -> None:
    from tools._pdf_sections import PdfSectionError, read_pdf_section

    pdf = _make_pdf(tmp_path, 1, toc=[[1, "Alpha", 1]])
    with pytest.raises(PdfSectionError):
        read_pdf_section(pdf, "Missing")
