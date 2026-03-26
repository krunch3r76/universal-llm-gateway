"""Document OCR tool registration — dispatch-only tools for scanned PDFs.

Phase 4: Extends the finance pipeline to handle scanned documents (no text
layer) by rendering pages to images and sending to Claude Vision.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._document_ocr import ocr_pages, ocr_structured
from ._file_helpers import FILES_ROOT, resolve_files_path
from ._finance_schemas import VALID_TYPES

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"})
_SCANNABLE = frozenset({".pdf"}) | _IMAGE_SUFFIXES


def register_document_ocr_tools(mcp: FastMCP) -> None:
    """Register document OCR tools (dispatch-only)."""

    @mcp.tool()
    def document_ocr(
        path: str,
        prompt: str = "",
        pages: list[int] | None = None,
        dpi: int = 200,
        model: str = "",
    ) -> dict[str, Any]:
        """OCR a scanned PDF or image via Claude Vision.

        Use when: a PDF has no text layer (pdfplumber returns empty/garbage),
        or you need to extract text from photographs or scanned documents.
        Returns per-page text with token usage.

        For financial documents that need structured JSON, prefer
        document_ocr_structured instead.

        Args:
            path: PDF or image path relative to /data/files/.
            prompt: Extraction instruction (default: generic text extraction).
            pages: 1-based page numbers to process (default: all).
            dpi: Render resolution for PDFs (default: 200).
            model: Model override (default: Claude Sonnet via Anthropic API).
        """
        t0 = monotonic_now()
        record("mcp.document.ocr.called", path=path)

        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if abs_path.suffix.lower() not in _SCANNABLE:
            raise ValueError(f"Unsupported file type: {abs_path.suffix!r}")

        kwargs: dict[str, Any] = {"dpi": dpi}
        if prompt:
            kwargs["prompt"] = prompt
        if pages:
            kwargs["pages"] = pages
        if model:
            kwargs["model"] = model

        result = ocr_pages(abs_path, **kwargs)
        result["path"] = path

        elapsed = monotonic_now() - t0
        record(
            "mcp.document.ocr.completed",
            path=path,
            pages=result["pages"],
            total_tokens=result["total_tokens"],
            duration_s=round(elapsed, 3),
        )
        return result

    @mcp.tool()
    def document_ocr_structured(
        path: str,
        statement_type: str,
        dpi: int = 200,
        model: str = "",
    ) -> dict[str, Any]:
        """OCR a scanned financial document directly into structured JSON.

        The scanned-document equivalent of finance_parse_statement. Renders
        pages to images, sends to Claude Vision with the statement type schema,
        returns structured JSON that feeds into finance_ingest_statement.

        Use when: you have a scanned PDF (no text layer) of a financial
        statement, tax form, or property tax bill.

        Args:
            path: PDF or image path relative to /data/files/.
            statement_type: One of the valid finance statement types.
            dpi: Render resolution (default: 200).
            model: Model override (default: Claude Sonnet via Anthropic API).
        """
        t0 = monotonic_now()
        record(
            "mcp.document.ocr.structured.called",
            path=path,
            statement_type=statement_type,
        )

        if statement_type not in VALID_TYPES:
            raise ValueError(
                f"Invalid statement_type: {statement_type!r}. "
                f"Valid: {sorted(VALID_TYPES)}"
            )

        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if abs_path.suffix.lower() not in _SCANNABLE:
            raise ValueError(f"Unsupported file type: {abs_path.suffix!r}")

        kwargs: dict[str, Any] = {"dpi": dpi}
        if model:
            kwargs["model"] = model

        result = ocr_structured(abs_path, statement_type, **kwargs)
        result["path"] = path

        elapsed = monotonic_now() - t0
        has_error = "error" in result
        record(
            "mcp.document.ocr.structured.completed"
            if not has_error
            else "mcp.document.ocr.structured.error",
            path=path,
            statement_type=statement_type,
            duration_s=round(elapsed, 3),
            **({"error": result["error"]} if has_error else {}),
        )
        return result

    @mcp.tool()
    def document_ocr_directory(
        directory: str,
        prompt: str = "",
        dpi: int = 200,
        model: str = "",
    ) -> dict[str, Any]:
        """Batch OCR all PDFs and images in a directory.

        Use when: bulk processing scanned documents (legal correspondence,
        assessor letters, deeds, etc.). Returns per-file OCR text.

        Args:
            directory: Directory path relative to /data/files/.
            prompt: Extraction instruction (default: generic text extraction).
            dpi: Render resolution for PDFs (default: 200).
            model: Model override (default: Claude Sonnet via Anthropic API).
        """
        t0 = monotonic_now()
        record("mcp.document.ocr.directory.called", directory=directory)

        abs_dir = resolve_files_path(directory)
        if not abs_dir.exists():
            raise FileNotFoundError(f"Directory not found: {directory!r}")
        if not abs_dir.is_dir():
            raise ValueError(f"Not a directory: {directory!r}")

        files = sorted(f for f in abs_dir.iterdir() if f.suffix.lower() in _SCANNABLE)
        if not files:
            raise FileNotFoundError(f"No scannable files found in {directory!r}")

        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for file_path in files:
            rel_path = str(file_path.relative_to(FILES_ROOT))
            try:
                kwargs: dict[str, Any] = {"dpi": dpi}
                if prompt:
                    kwargs["prompt"] = prompt
                if model:
                    kwargs["model"] = model

                result = ocr_pages(file_path, **kwargs)
                result["path"] = rel_path
                results.append(result)
            except Exception as exc:
                logger.warning("Failed to OCR %s: %s", rel_path, exc)
                errors.append({"path": rel_path, "error": str(exc)})

        elapsed = monotonic_now() - t0
        record(
            "mcp.document.ocr.directory.completed",
            directory=directory,
            file_count=len(files),
            success_count=len(results),
            error_count=len(errors),
            duration_s=round(elapsed, 3),
        )
        return {
            "directory": directory,
            "results": results,
            "errors": errors,
            "summary": {
                "total_files": len(files),
                "successful": len(results),
                "failed": len(errors),
            },
        }
