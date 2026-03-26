"""Parser for records.txt — the machine-readable side of a human↔machine contract.

Format: indented block records separated by blank lines.
  First line of each block = issuer/payee name (+ optional date info)
  Indented lines (4 spaces) = Key: Value fields

See notes/system/specs/records-format.md for the full spec.

Example:
    West Valley Recycles Statement Date 02/01/2026
        Type: utility
        Account number: 4025-8743914
        Amount due: $229.08
        Date due: 04/25/2026
        Balance: $9,720.54
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class BlockRecord:
    """A full parsed block from records.txt before entity resolution."""

    issuer: str
    fields: dict[str, str]
    line_start: int
    line_end: int


def _normalize_date(d: str) -> str | None:
    """Convert MM/DD/YYYY → YYYY-MM-DD. Returns None on failure."""
    d = d.strip()
    if not d or d.lower() == "null":
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if re.match(r"\d{4}-\d{2}-\d{2}", d):
        return d
    return None


def _parse_blocks(content: str) -> tuple[list[BlockRecord], list[dict[str, Any]]]:
    """Split content into indented blocks, return blocks and parse errors."""
    blocks: list[BlockRecord] = []
    errors: list[dict[str, Any]] = []
    lines = content.splitlines()

    header_patterns = (
        "records to ingest",
        "note to entity ingestion",
        "for bills divided",
    )

    current_issuer: str | None = None
    current_fields: dict[str, str] = {}
    current_start = 0
    in_multiline_key: str | None = None

    def _flush() -> None:
        nonlocal current_issuer, current_fields, current_start, in_multiline_key
        if current_issuer and current_fields:
            blocks.append(
                BlockRecord(
                    issuer=current_issuer,
                    fields=current_fields,
                    line_start=current_start,
                    line_end=len(lines),
                )
            )
        current_issuer = None
        current_fields = {}
        in_multiline_key = None

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            _flush()
            continue

        if stripped.startswith("#"):
            continue

        if any(stripped.lower().startswith(h) for h in header_patterns):
            continue

        if line.startswith("    ") or line.startswith("\t"):
            if current_issuer is None:
                continue

            field_match = re.match(r"\s+\*?([^:]+):\s*(.*)", line)
            if field_match:
                key = field_match.group(1).strip().lower().replace(" ", "_")
                val = field_match.group(2).strip()
                current_fields[key] = val
                in_multiline_key = key
            elif in_multiline_key:
                current_fields[in_multiline_key] += " " + stripped
        else:
            _flush()
            current_issuer = stripped
            current_fields = {}
            current_start = i + 1
            in_multiline_key = None

    _flush()
    return blocks, errors


def normalize_amount(value: str) -> float | None:
    """Normalize a currency string to a float for comparison.

    Handles: "$1,234.56", "1234.56", "$-50.00", etc.
    """
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None
