"""Phase 3 finance tools: Cortex ingestion from parsed financial statements.

Registers finance_ingest_statement and finance_ingest_directory as
dispatch-only tools. Both route through _finance_ingest.ingest_statement.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._file_helpers import FILES_ROOT, resolve_files_path
from ._finance_ingest import ingest_statement
from ._finance_schemas import VALID_TYPES
from .finance import _parse_statement

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_finance_ingest_tools(mcp: FastMCP) -> None:
    """Register financial statement → Cortex ingestion tools (dispatch-only)."""

    @mcp.tool()
    def finance_ingest_statement(
        parsed_json: dict[str, Any] | None = None,
        path: str | None = None,
        statement_type: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a parsed financial statement into Cortex entities + assertions.

        Two input modes:
          End-to-end: pass path + statement_type — calls Phase 2 parser, then ingests.
          Direct: pass parsed_json + statement_type — ingests pre-parsed data
              (for debugging or re-ingestion without re-parsing).

        Creates account, organization, and statement entities with temporally
        scoped assertions. Idempotent via content_hash — re-running with the
        same PDF returns already_ingested.

        Prefer finance_ingest_directory for batch monthly ingestion.

        Args:
            parsed_json: Pre-parsed Phase 2 output dict (direct mode).
            path: PDF path relative to /data/files/ sandbox (end-to-end mode).
            statement_type: One of checking, credit_card, utility, phone, ploc,
                student_loan, brokerage, tax_document, property_tax, mortgage, escrow.
        """
        t0 = monotonic_now()

        if parsed_json is not None:
            if not statement_type:
                raise ValueError("statement_type is required with parsed_json")
            if statement_type not in VALID_TYPES:
                raise ValueError(
                    f"Invalid statement_type: {statement_type!r}. "
                    f"Valid: {sorted(VALID_TYPES)}"
                )
            pdf_path = path or "unknown"
            record("mcp.finance.ingest.called", mode="direct", path=pdf_path)
            result = ingest_statement(parsed_json, statement_type, pdf_path)
            elapsed = monotonic_now() - t0
            record(
                "mcp.finance.ingest.tool.done",
                mode="direct",
                status=result.get("status"),
                duration_s=round(elapsed, 3),
            )
            return result

        if not path:
            raise ValueError("Either parsed_json or path is required")
        if not statement_type:
            raise ValueError("statement_type is required with path")
        if statement_type not in VALID_TYPES:
            raise ValueError(
                f"Invalid statement_type: {statement_type!r}. "
                f"Valid: {sorted(VALID_TYPES)}"
            )

        record("mcp.finance.ingest.called", mode="e2e", path=path)
        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"PDF not found: {path!r}")
        if abs_path.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF: {path!r}")

        parse_result = _parse_statement(abs_path, path, statement_type)
        if "error" in parse_result:
            return {
                "status": "error",
                "error": parse_result["error"],
                "phase": "parse",
                "path": path,
            }

        result = ingest_statement(parse_result["parsed"], statement_type, path)
        elapsed = monotonic_now() - t0
        record(
            "mcp.finance.ingest.tool.done",
            mode="e2e",
            status=result.get("status"),
            duration_s=round(elapsed, 3),
        )
        return result

    @mcp.tool()
    def finance_ingest_directory(
        directory: str,
        type_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Ingest all PDFs in a directory into Cortex via end-to-end pipeline.

        The one-command monthly ingestion: drop statements in a folder, run one
        call, Cortex entities and assertions updated.

        Requires type_map to route each PDF to the correct statement type.
        Same convention as finance_parse_directory.

        Args:
            directory: relative to /data/files/ sandbox.
            type_map: maps filename patterns to statement types, e.g.
                {"wf_cc": "credit_card", "pge": "utility", "chase_chk": "checking"}.
        """
        t0 = monotonic_now()
        record("mcp.finance.ingest.directory.called", directory=directory)

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
                f"types. Valid types: {sorted(VALID_TYPES)}"
            )
        for pattern, stype in type_map.items():
            if stype not in VALID_TYPES:
                raise ValueError(
                    f"Invalid type {stype!r} for pattern {pattern!r}. "
                    f"Valid: {sorted(VALID_TYPES)}"
                )

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        entities_created = 0
        assertions_total = 0

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
                parse_result = _parse_statement(pdf_path, rel_path, matched_type)
                if "error" in parse_result:
                    errors.append(
                        {
                            "path": rel_path,
                            "error": parse_result["error"],
                            "phase": "parse",
                        }
                    )
                    continue

                result = ingest_statement(
                    parse_result["parsed"], matched_type, rel_path
                )
                results.append({"path": rel_path, **result})
                if result.get("status") == "ingested":
                    entities_created += 3
                    assertions_total += result.get("assertions_created", 0)
            except Exception as exc:
                logger.warning("Failed to ingest %s: %s", rel_path, exc)
                errors.append({"path": rel_path, "error": str(exc)})

        elapsed = monotonic_now() - t0
        record(
            "mcp.finance.ingest.directory.completed",
            directory=directory,
            pdf_count=len(pdfs),
            ingested=len(results),
            errors=len(errors),
            skipped=len(skipped),
            entities_created=entities_created,
            assertions_total=assertions_total,
            duration_s=round(elapsed, 3),
        )
        return {
            "directory": directory,
            "results": results,
            "errors": errors,
            "skipped": skipped,
            "summary": {
                "total_pdfs": len(pdfs),
                "ingested": len(results),
                "failed": len(errors),
                "skipped": len(skipped),
                "entities_created": entities_created,
                "assertions_seeded": assertions_total,
            },
        }
