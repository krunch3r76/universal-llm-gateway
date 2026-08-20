"""Format-specific file writers: DOCX and PDF."""

from __future__ import annotations

from pathlib import Path


def write_docx(path: Path, content: str) -> None:
    from docx import Document

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for para in content.split("\n"):
        doc.add_paragraph(para)
    doc.save(str(path))


def write_pdf(path: Path, content: str) -> None:
    from fpdf import FPDF

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in content.split("\n"):
        pdf.multi_cell(0, 6, txt=line or " ")
    pdf.output(str(path))
