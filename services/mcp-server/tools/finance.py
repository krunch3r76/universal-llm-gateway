"""Financial PDF extraction and parsing tools.

Phase 1: pdfplumber-based table and text extraction (deterministic).
Phase 2: Claude API structured parsing via llm_generate (LLM-powered).
"""

from __future__ import annotations

import json as _json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._file_helpers import FILES_ROOT, resolve_files_path
from ._finance_schemas import STATEMENT_SCHEMAS, VALID_TYPES
from .llm import _call_anthropic

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


# -- Phase 1: raw extraction ------------------------------------------------


def _extract_pdf(abs_path: Path) -> dict[str, Any]:
    """Extract tables and text from a single PDF using pdfplumber."""
    import pdfplumber

    pages: list[dict[str, Any]] = []
    pdf_metadata: dict[str, Any] = {}

    with pdfplumber.open(abs_path) as pdf:
        pdf_metadata = {
            k: v
            for k, v in (pdf.metadata or {}).items()
            if isinstance(v, str | int | float | bool)
        }
        for i, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or []
            text = page.extract_text() or ""
            pages.append(
                {
                    "page_number": i,
                    "tables": tables,
                    "text": text,
                }
            )

    return {
        "pages": pages,
        "metadata": {
            "page_count": len(pages),
            "pdf_metadata": pdf_metadata,
        },
    }


# -- Phase 2: LLM-powered parsing -------------------------------------------

_PARSE_MODEL = "claude-sonnet-4-20250514"
_PARSE_MAX_TOKENS = 8192
_SYSTEM_PROMPT = (
    "You are a financial document parser. Extract structured data from this "
    "bank statement. Return ONLY valid JSON with no preamble, no markdown, "
    "no explanation."
)
_VALID_TYPES = VALID_TYPES
_STATEMENT_SCHEMAS = STATEMENT_SCHEMAS

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _build_user_prompt(statement_type: str, extraction: dict[str, Any]) -> str:
    """Concatenate extracted PDF content and target schema into a user prompt."""
    all_text = "\n\n".join(p["text"] for p in extraction["pages"] if p.get("text"))
    all_tables: list[list[list[str | None]]] = []
    for page in extraction["pages"]:
        if page.get("tables"):
            all_tables.extend(page["tables"])
    schema_json = _json.dumps(_STATEMENT_SCHEMAS[statement_type], indent=2)
    tables_section = _json.dumps(all_tables, indent=2) if all_tables else "None"
    return (
        f"Statement type: {statement_type}\n\n"
        f"Extracted text from PDF:\n{all_text}\n\n"
        f"Extracted tables:\n{tables_section}\n\n"
        f"Return JSON matching this schema:\n{schema_json}"
    )


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """Strip markdown fences (if any) and parse JSON from LLM output."""
    text = raw.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    return _json.loads(text)


def _parse_statement(
    abs_path: Path, rel_path: str, statement_type: str
) -> dict[str, Any]:
    """Extract PDF → build prompt → call Claude → parse JSON response."""
    extraction = _extract_pdf(abs_path)
    user_prompt = _build_user_prompt(statement_type, extraction)
    payload: dict[str, Any] = {
        "model": _PARSE_MODEL,
        "max_tokens": _PARSE_MAX_TOKENS,
        "messages": [{"role": "user", "content": user_prompt}],
        "system": _SYSTEM_PROMPT,
    }
    llm_result = _call_anthropic(payload, requested_model=_PARSE_MODEL)
    if "error" in llm_result:
        return {
            "path": rel_path,
            "statement_type": statement_type,
            "error": llm_result["error"],
            "raw_response": llm_result,
        }
    content_blocks = llm_result.get("content", [])
    raw_text = "".join(
        b.get("text", "") for b in content_blocks if b.get("type") == "text"
    )
    try:
        parsed = _parse_llm_json(raw_text)
    except (_json.JSONDecodeError, ValueError) as exc:
        return {
            "path": rel_path,
            "statement_type": statement_type,
            "error": f"JSON parse failed: {exc}",
            "raw_response": raw_text,
        }
    return {"path": rel_path, "statement_type": statement_type, "parsed": parsed}


# -- Tool registration -------------------------------------------------------


def register_finance_tools(mcp: FastMCP) -> None:
    """Register financial PDF extraction and parsing tools (dispatch-only)."""

    @mcp.tool()
    def finance_extract_pdf(path: str) -> dict[str, Any]:
        """Extract tables and text from a financial PDF using pdfplumber.

        Use when: processing bank statements, credit card statements, utility
        bills, or any columnar PDF. Returns raw table arrays (from
        pdfplumber's extract_tables()) and full page text for each page.

        Prefer this over files(op="read") for financial PDFs — pdfplumber
        preserves table structure that pymupdf4llm loses.

        Args:
            path: relative to /data/files/ sandbox
                  (e.g. "dropbox/cortex_finance/ingest_03-22-2026/02_wf_cc.pdf")
        """
        t0 = monotonic_now()
        record("mcp.finance.extract.called", path=path)

        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"PDF not found: {path!r}")
        if abs_path.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF: {path!r} (suffix: {abs_path.suffix!r})")

        result = _extract_pdf(abs_path)
        result["path"] = path

        elapsed = monotonic_now() - t0
        record(
            "mcp.finance.extract.completed",
            path=path,
            page_count=result["metadata"]["page_count"],
            duration_ms=round(elapsed),
        )
        return result

    @mcp.tool()
    def finance_extract_directory(directory: str) -> dict[str, Any]:
        """Extract tables and text from every PDF in a directory.

        Use when: batch-processing a month's worth of statements. Runs
        finance_extract_pdf on each .pdf file found in the directory.

        Args:
            directory: relative to /data/files/ sandbox
                       (e.g. "dropbox/cortex_finance/ingest_03-22-2026")
        """
        t0 = monotonic_now()
        record("mcp.finance.extract.directory.called", directory=directory)

        abs_dir = resolve_files_path(directory)
        if not abs_dir.exists():
            raise FileNotFoundError(f"Directory not found: {directory!r}")
        if not abs_dir.is_dir():
            raise ValueError(f"Not a directory: {directory!r}")

        pdfs = sorted(abs_dir.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"No PDFs found in {directory!r}")

        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for pdf_path in pdfs:
            rel_path = str(pdf_path.relative_to(FILES_ROOT))
            try:
                extracted = _extract_pdf(pdf_path)
                extracted["path"] = rel_path
                results.append(extracted)
            except Exception as exc:
                logger.warning("Failed to extract %s: %s", rel_path, exc)
                errors.append({"path": rel_path, "error": str(exc)})

        elapsed = monotonic_now() - t0
        record(
            "mcp.finance.extract.directory.completed",
            directory=directory,
            pdf_count=len(pdfs),
            success_count=len(results),
            error_count=len(errors),
            duration_ms=round(elapsed),
        )
        return {
            "directory": directory,
            "results": results,
            "errors": errors,
            "summary": {
                "total_pdfs": len(pdfs),
                "successful": len(results),
                "failed": len(errors),
            },
        }

    @mcp.tool()
    def finance_parse_statement(path: str, statement_type: str) -> dict[str, Any]:
        """Parse a financial PDF into structured JSON via Claude API.

        Use when: you need structured, schema-conformant JSON from a bank
        statement, credit card bill, utility bill, or similar. Extracts raw
        content via pdfplumber, then sends to Claude Sonnet for structured
        extraction.

        Prefer finance_extract_pdf for raw debugging/inspection. Use this
        tool when you want the final parsed output.

        Args:
            path: PDF path relative to /data/files/ sandbox.
            statement_type: one of checking, credit_card, utility, phone, ploc.
        """
        t0 = monotonic_now()
        record("mcp.finance.parse.called", path=path, statement_type=statement_type)

        if statement_type not in _VALID_TYPES:
            raise ValueError(
                f"Invalid statement_type: {statement_type!r}. "
                f"Valid: {sorted(_VALID_TYPES)}"
            )

        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"PDF not found: {path!r}")
        if abs_path.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF: {path!r}")

        result = _parse_statement(abs_path, path, statement_type)

        elapsed = monotonic_now() - t0
        has_error = "error" in result
        record(
            "mcp.finance.parse.completed"
            if not has_error
            else "mcp.finance.parse.error",
            path=path,
            statement_type=statement_type,
            duration_s=round(elapsed, 3),
            **({"error": result["error"]} if has_error else {}),
        )
        return result

    @mcp.tool()
    def finance_parse_directory(
        directory: str,
        type_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Parse all PDFs in a directory into structured JSON via Claude API.

        Use when: batch-parsing a month's statements. Requires a type_map
        that maps filename substrings to statement types so each PDF is
        routed to the correct extraction schema.

        Args:
            directory: relative to /data/files/ sandbox.
            type_map: maps filename patterns to statement types, e.g.
                {"wf_cc": "credit_card", "pge": "utility", "chase_chk": "checking"}.
                A PDF matches if any key appears (case-insensitive) in its stem.
        """
        t0 = monotonic_now()
        record("mcp.finance.parse.directory.called", directory=directory)

        abs_dir = resolve_files_path(directory)
        if not abs_dir.exists():
            raise FileNotFoundError(f"Directory not found: {directory!r}")
        if not abs_dir.is_dir():
            raise ValueError(f"Not a directory: {directory!r}")

        pdfs = sorted(abs_dir.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"No PDFs found in {directory!r}")

        if type_map is None:
            raise ValueError(
                "type_map is required — maps filename patterns to statement "
                f"types. Valid types: {sorted(_VALID_TYPES)}"
            )
        for pattern, stype in type_map.items():
            if stype not in _VALID_TYPES:
                raise ValueError(
                    f"Invalid type {stype!r} for pattern {pattern!r}. "
                    f"Valid: {sorted(_VALID_TYPES)}"
                )

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []

        for pdf_path in pdfs:
            rel_path = str(pdf_path.relative_to(FILES_ROOT))
            filename_lower = pdf_path.stem.lower()

            matched_type: str | None = None
            for pattern, stype in type_map.items():
                if pattern.lower() in filename_lower:
                    matched_type = stype
                    break

            if matched_type is None:
                skipped.append({"path": rel_path, "reason": "no type_map match"})
                continue

            try:
                result = _parse_statement(pdf_path, rel_path, matched_type)
                results.append(result)
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", rel_path, exc)
                errors.append({"path": rel_path, "error": str(exc)})

        elapsed = monotonic_now() - t0
        record(
            "mcp.finance.parse.directory.completed",
            directory=directory,
            pdf_count=len(pdfs),
            parsed_count=len(results),
            error_count=len(errors),
            skipped_count=len(skipped),
            duration_s=round(elapsed, 3),
        )
        return {
            "directory": directory,
            "results": results,
            "errors": errors,
            "skipped": skipped,
            "summary": {
                "total_pdfs": len(pdfs),
                "parsed": len(results),
                "failed": len(errors),
                "skipped": len(skipped),
            },
        }
