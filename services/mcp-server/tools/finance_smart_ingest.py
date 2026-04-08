"""Smart ingest tool: auto-detect statement type from PDF content.

Eliminates the type_map requirement by using keyword scoring on extracted
text. Falls back to Claude API classification only when ambiguous.
"""

from __future__ import annotations

import json as _json
import logging
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._file_helpers import FILES_ROOT, resolve_files_path
from ._finance_detect import detect_statement_type, is_confident
from ._finance_ingest import ingest_statement
from ._finance_schemas import VALID_TYPES
from .finance import _extract_pdf, _parse_statement
from .llm import _call_anthropic

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_CLASSIFY_MODEL = "claude-sonnet-4-20250514"
_CLASSIFY_MAX_TOKENS = 256
_CLASSIFY_SYSTEM = (
    "You are a financial document classifier. Given extracted text from a PDF, "
    "determine the statement type. Respond with ONLY a JSON object: "
    '{"type": "<type>"} where type is one of: '
    + ", ".join(sorted(VALID_TYPES))
    + ". No preamble, no explanation."
)


def _llm_classify(text: str) -> str | None:
    """Use Claude API to classify ambiguous statement types."""
    payload: dict[str, Any] = {
        "model": _CLASSIFY_MODEL,
        "max_tokens": _CLASSIFY_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": f"Classify this financial document:\n\n{text[:4000]}",
            }
        ],
        "system": _CLASSIFY_SYSTEM,
    }
    result = _call_anthropic(payload, requested_model=_CLASSIFY_MODEL)
    if "error" in result:
        logger.warning("LLM classification failed: %s", result["error"])
        return None
    content_blocks = result.get("content", [])
    raw = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
    try:
        parsed = _json.loads(raw.strip())
        detected = parsed.get("type", "")
        if detected in VALID_TYPES:
            return detected
        logger.warning("LLM returned invalid type: %s", detected)
        return None
    except (_json.JSONDecodeError, ValueError) as exc:
        logger.warning("LLM classification JSON parse failed: %s", exc)
        return None


def register_finance_smart_ingest_tools(mcp: FastMCP) -> None:
    """Register the finance_smart_ingest tool."""

    @mcp.tool(title="Finance: Smart Ingest")
    def finance_smart_ingest(
        path: str,
    ) -> dict[str, Any]:
        """Auto-detect statement type and ingest a financial PDF into Cortex.

        Use when: you have a financial PDF but don't know the statement type,
        or when batch-processing mixed statement folders. Eliminates the need
        for a type_map.

        Detection strategy:
          1. Extract PDF text via pdfplumber
          2. Score against keyword patterns for each statement type
          3. If confident (clear winner) → use detected type
          4. If ambiguous → fall back to Claude API classification
          5. Parse and ingest via the standard pipeline

        Args:
            path: PDF path relative to /data/files/ sandbox.
        """
        t0 = monotonic_now()
        record("mcp.finance.smart.ingest.called", path=path)

        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"PDF not found: {path!r}")
        if abs_path.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF: {path!r}")

        extraction = _extract_pdf(abs_path)
        detected_type, margin, scores = detect_statement_type(extraction)

        detection_method = "keyword"
        if detected_type is None:
            all_text = "\n".join(p.get("text", "") for p in extraction.get("pages", []))
            detected_type = _llm_classify(all_text)
            detection_method = "llm"
            if detected_type is None:
                elapsed = monotonic_now() - t0
                record(
                    "mcp.finance.smart.ingest.failed",
                    path=path,
                    reason="unclassifiable",
                    duration_ms=round(elapsed),
                )
                return {
                    "status": "error",
                    "error": "Could not determine statement type",
                    "path": path,
                    "scores": scores[:5],
                }

        if not is_confident(margin) and detection_method == "keyword":
            all_text = "\n".join(p.get("text", "") for p in extraction.get("pages", []))
            llm_type = _llm_classify(all_text)
            if llm_type:
                detected_type = llm_type
                detection_method = "llm_override"

        rel_path = str(abs_path.relative_to(FILES_ROOT))
        parse_result = _parse_statement(abs_path, rel_path, detected_type)
        if "error" in parse_result:
            elapsed = monotonic_now() - t0
            record(
                "mcp.finance.smart.ingest.failed",
                path=path,
                detected_type=detected_type,
                reason="parse_error",
                duration_ms=round(elapsed),
            )
            return {
                "status": "error",
                "error": parse_result["error"],
                "phase": "parse",
                "path": path,
                "detected_type": detected_type,
                "detection_method": detection_method,
            }

        ingest_result = ingest_statement(
            parse_result["parsed"], detected_type, rel_path
        )
        elapsed = monotonic_now() - t0
        record(
            "mcp.finance.smart.ingest.completed",
            path=path,
            detected_type=detected_type,
            detection_method=detection_method,
            status=ingest_result.get("status"),
            duration_ms=round(elapsed),
        )

        return {
            **ingest_result,
            "detected_type": detected_type,
            "detection_method": detection_method,
            "detection_margin": round(margin, 3),
            "top_scores": scores[:3],
        }

    @mcp.tool(title="Finance: Smart Ingest Directory")
    def finance_smart_ingest_directory(
        directory: str,
    ) -> dict[str, Any]:
        """Auto-detect and ingest all PDFs in a directory into Cortex.

        Use when: batch-processing a folder of mixed financial statements
        without a type_map. Each PDF is individually classified and ingested.

        Args:
            directory: relative to /data/files/ sandbox.
        """
        t0 = monotonic_now()
        record("mcp.finance.smart.ingest.directory.called", directory=directory)

        abs_dir = resolve_files_path(directory)
        if not abs_dir.exists():
            raise FileNotFoundError(f"Directory not found: {directory!r}")
        if not abs_dir.is_dir():
            raise ValueError(f"Not a directory: {directory!r}")

        pdfs = sorted(abs_dir.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"No PDFs found in {directory!r}")

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for pdf_path in pdfs:
            rel_path = str(pdf_path.relative_to(FILES_ROOT))
            try:
                extraction = _extract_pdf(pdf_path)
                detected_type, margin, scores = detect_statement_type(extraction)

                method = "keyword"
                if detected_type is None or not is_confident(margin):
                    all_text = "\n".join(
                        p.get("text", "") for p in extraction.get("pages", [])
                    )
                    llm_type = _llm_classify(all_text)
                    if llm_type:
                        detected_type = llm_type
                        method = "llm" if detected_type is None else "llm_override"

                if detected_type is None:
                    errors.append(
                        {
                            "path": rel_path,
                            "error": "Could not determine statement type",
                            "scores": scores[:3],
                        }
                    )
                    continue

                parse_result = _parse_statement(pdf_path, rel_path, detected_type)
                if "error" in parse_result:
                    errors.append(
                        {
                            "path": rel_path,
                            "error": parse_result["error"],
                            "phase": "parse",
                            "detected_type": detected_type,
                        }
                    )
                    continue

                result = ingest_statement(
                    parse_result["parsed"], detected_type, rel_path
                )
                results.append(
                    {
                        "path": rel_path,
                        "detected_type": detected_type,
                        "detection_method": method,
                        **result,
                    }
                )
            except Exception as exc:
                logger.warning("Smart ingest failed for %s: %s", rel_path, exc)
                errors.append({"path": rel_path, "error": str(exc)})

        elapsed = monotonic_now() - t0
        record(
            "mcp.finance.smart.ingest.directory.completed",
            directory=directory,
            pdf_count=len(pdfs),
            ingested=len(results),
            errors=len(errors),
            duration_ms=round(elapsed),
        )
        return {
            "directory": directory,
            "results": results,
            "errors": errors,
            "summary": {
                "total_pdfs": len(pdfs),
                "ingested": len(results),
                "failed": len(errors),
            },
        }
