"""Document OCR tool registration for scanned PDFs and images."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._document_ocr import ocr_pages
from ._file_helpers import FILES_ROOT, resolve_files_path

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"})
_SCANNABLE = frozenset({".pdf"}) | _IMAGE_SUFFIXES


def register_document_ocr_tools(mcp: FastMCP) -> None:
    """Register general-purpose document OCR tools (public dispatch)."""

    @mcp.tool(title="Document OCR")
    def document_ocr(
        path: str,
        prompt: str = "",
        pages: list[int] | None = None,
        dpi: int = 200,
        model: str = "",
    ) -> dict[str, Any]:
        """OCR a scanned PDF or image via a frontier vision model (Stargate-routed).

        Available via: dispatch(tool="document_ocr", arguments='{"path": "..."}')

        Use when: fs(op="read") returns empty or garbled text for a PDF (no text
        layer), or you need to extract text from photographs or scanned documents.
        Handles rendering, resizing, batching, and vision model routing server-side
        — do not extract base64 manually.

        For financial documents that need structured JSON, prefer
        document_ocr_structured (via private_dispatch) instead.

        Args:
            path: PDF or image path relative to /data/files/.
            prompt: Extraction instruction (default: generic text extraction).
            pages: 1-based page numbers to process (default: all).
            dpi: Render resolution for PDFs (default: 200).
            model: Model override (default: openai/gpt-5.4). Any Stargate-routable
                vision model works — e.g. anthropic/claude-sonnet-4,
                xai/grok-4.20-0309-reasoning.
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
        result["_next"] = (
            "If extracted text contains facts about known entities, "
            "seed via cortex assert or ingest_document. "
            "If the source document lacks a document: entity in Cortex, "
            "create one via cortex entity_create"
        )
        return result

    @mcp.tool(title="Document OCR (Directory)")
    def document_ocr_directory(
        directory: str,
        prompt: str = "",
        dpi: int = 200,
        model: str = "",
    ) -> dict[str, Any]:
        """Batch OCR all PDFs and images in a directory.

        Available via: dispatch(tool="document_ocr_directory", ...)

        Cost warning: runs one vision model call per page per file — a
        directory with 20 multi-page PDFs can consume thousands of tokens.
        Prefer document_ocr on individual files when possible.

        Args:
            directory: Directory path relative to /data/files/.
            prompt: Extraction instruction (default: generic text extraction).
            dpi: Render resolution for PDFs (default: 200).
            model: Model override (default: openai/gpt-5.4). Any Stargate-routable
                vision model works — e.g. anthropic/claude-sonnet-4,
                xai/grok-4.20-0309-reasoning.
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
