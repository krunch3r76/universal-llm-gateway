"""Smart document ingestion — detect format, extract text, write sidecar markdown.

One-shot tool for agents: give it a path to any document (text PDF, scanned PDF,
image, ODT, DOCX, EML) and get back extracted text persisted as markdown.
Optionally registers a Cortex document entity.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._document_ocr import has_text_layer, ocr_pages
from ._file_helpers import FILES_ROOT, resolve_files_path

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_TEXT_EXTRACTABLE = frozenset({".pdf", ".docx", ".odt", ".eml", ".html", ".txt", ".md"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"})
_ALL_SUPPORTED = _TEXT_EXTRACTABLE | _IMAGE_SUFFIXES


def _detect_format(path: Path) -> str:
    """Classify a file into a processing strategy.

    Returns one of: 'text_pdf', 'scanned_pdf', 'image', 'rich_text', 'plain_text'.
    """
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pdf":
        if has_text_layer(path):
            return "text_pdf"
        return "scanned_pdf"
    if suffix in (".docx", ".odt", ".eml", ".html"):
        return "rich_text"
    return "plain_text"


def _extract_text(path: Path, fmt: str, dpi: int, model: str) -> str:
    """Extract text from *path* using the strategy indicated by *fmt*."""
    if fmt == "text_pdf":
        from ._file_helpers import _read_pdf

        return _read_pdf(path)

    if fmt in ("scanned_pdf", "image"):
        kwargs: dict[str, Any] = {"dpi": dpi}
        if model:
            kwargs["model"] = model
        result = ocr_pages(path, **kwargs)
        return result.get("text", "")

    if fmt == "rich_text":
        suffix = path.suffix.lower()
        if suffix == ".docx":
            from ._file_helpers import _read_docx

            return _read_docx(path)
        if suffix == ".odt":
            from ._file_helpers import _read_odt

            return _read_odt(path)
        if suffix == ".eml":
            from ._file_helpers import _read_eml

            return _read_eml(path)
        return path.read_text(encoding="utf-8", errors="replace")

    return path.read_text(encoding="utf-8", errors="replace")


def _write_markdown(
    output_path: Path, source_rel: str, fmt: str, text: str
) -> None:
    """Write extracted text as markdown with a provenance header."""
    header = (
        f"# Extracted: {Path(source_rel).name}\n\n"
        f"- **Source**: `{source_rel}`\n"
        f"- **Method**: {fmt}\n\n---\n\n"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(header + text, encoding="utf-8")


def register_ingest_document_tools(mcp: FastMCP) -> None:
    """Register the ingest_document tool."""

    @mcp.tool()
    def ingest_document(
        path: str,
        output_path: str = "",
        dpi: int = 200,
        model: str = "",
        entity_id: str = "",
        entity_description: str = "",
    ) -> dict[str, Any]:
        """Ingest any document — detect format, extract text, write sidecar markdown.

        Smart router: automatically detects whether a file is a text PDF,
        scanned PDF, image, ODT, DOCX, EML, or plain text, then extracts
        content using the appropriate method (pymupdf4llm for text PDFs,
        Claude Vision OCR with auto-resize for scanned PDFs and images,
        python-docx/odfpy/email for rich formats).

        Use when: you need to read a document that isn't plain text or a
        text-layer PDF — scanned PDFs, high-res photos of documents, or
        any format where read_file returns empty/garbage.

        The extracted text is persisted as a markdown file so future reads
        don't re-run OCR.

        Args:
            path: File path relative to /data/files/.
            output_path: Where to write the extracted markdown. Defaults to
                ``{directory}/{stem}.extracted.md`` alongside the source.
            dpi: Render resolution for PDF→image conversion (default 200).
            model: OCR model override (default: Claude Sonnet via Anthropic).
            entity_id: Optional ``document:*`` Cortex entity ID to create.
            entity_description: Description for the Cortex entity.

        Returns:
            Metadata: path, output_path, format detected, chars extracted,
            and entity_id if created.
        """
        t0 = monotonic_now()
        record("mcp.document.ingest.called", path=path)

        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not abs_path.is_file():
            raise ValueError(f"Not a file: {path!r}")

        suffix = abs_path.suffix.lower()
        if suffix not in _ALL_SUPPORTED:
            raise ValueError(
                f"Unsupported format {suffix!r}. "
                f"Supported: {', '.join(sorted(_ALL_SUPPORTED))}"
            )

        fmt = _detect_format(abs_path)
        logger.info("ingest_document: %s detected as %s", path, fmt)

        text = _extract_text(abs_path, fmt, dpi, model)
        if not text.strip():
            record("mcp.document.ingest.empty", path=path, format=fmt)
            return {
                "path": path,
                "format": fmt,
                "chars": 0,
                "warning": "Extraction returned empty text",
            }

        if output_path:
            out_abs = resolve_files_path(output_path)
        else:
            out_abs = abs_path.with_suffix(".extracted.md")

        _write_markdown(out_abs, path, fmt, text)
        out_rel = str(out_abs.relative_to(FILES_ROOT))

        result: dict[str, Any] = {
            "path": path,
            "output_path": out_rel,
            "format": fmt,
            "chars": len(text),
        }

        if entity_id:
            from .local_api import _relay

            body: dict[str, Any] = {
                "id": entity_id,
                "type": "document",
                "name": Path(path).stem.replace("-", " ").replace("_", " ").title(),
                "source_uri": path,
            }
            if entity_description:
                body["description"] = entity_description
            resp = _relay("cortex-api", "POST", "/entities", body=body)
            if "error" in resp and resp.get("status_code") != 409:
                result["entity_warning"] = resp["error"]
            else:
                result["entity_id"] = entity_id
                result["entity_created"] = resp.get("status_code") != 409

        elapsed = monotonic_now() - t0
        record(
            "mcp.document.ingest.completed",
            path=path,
            format=fmt,
            chars=len(text),
            output_path=out_rel,
            duration_s=round(elapsed, 3),
        )
        logger.info(
            "ingest_document: %s → %s (%d chars, %.1fs)",
            path, out_rel, len(text), elapsed,
        )
        return result
