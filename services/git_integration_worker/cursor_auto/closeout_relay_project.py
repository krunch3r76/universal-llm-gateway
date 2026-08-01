"""Project authored §2 prose to a normalized relay table — zero silent field loss."""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    RELAY_CELL_CAP_CHARS,
    _table_cell,
    build_ac_verdict_cell,
    default_relay_cell_cap,
    fenced_cell_pointer,
    fill_judgment_cell,
    looks_fenced,
    relay_parse_miss_cell,
    sanitize_relay_cell,
    status_from_section2,
    strip_machine_tail,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
    extract_status,
    extract_table_field,
    field_heading_present,
)
from services.git_integration_worker.cursor_auto.section2_fields import SECTION2_FIELDS

_UNCLASSIFIED_RE = re.compile(
    r"(?:unclassified\s*[—-]\s*relay could not parse|parse_failed\s*[—-])",
    re.IGNORECASE,
)

_RELAY_PARSE_MISS_RE = re.compile(
    r"relay could not locate\s+`[^`]+`\s*[—-]\s*see source_ref:",
    re.IGNORECASE,
)


def count_unclassified_fields(body: str) -> int:
    """Return how many relay cells report §2 parse uncertainty."""
    return len(_UNCLASSIFIED_RE.findall(body))


def count_relay_parse_miss_fields(body: str) -> int:
    """Return how many relay cells honestly report a field the projector could not locate."""
    return len(_RELAY_PARSE_MISS_RE.findall(body))


def _extract_cell(prose: str, field: str, *, provenance: str) -> str:
    if field == "status":
        value = extract_status(prose)
        return value or ""
    if field == "ac_verdict":
        return build_ac_verdict_cell(prose, provenance=provenance, cap=None)
    direct = extract_field_section(prose, field) or extract_table_field(prose, field)
    if direct:
        if len(direct) > RELAY_CELL_CAP_CHARS:
            direct = default_relay_cell_cap(direct, provenance)
        return sanitize_relay_cell(direct, provenance)
    if not field_heading_present(prose, field):
        return relay_parse_miss_cell(field, provenance)
    return fill_judgment_cell(prose, field, provenance=provenance, cap=None)


def _append_fenced_sections(
    lines: list[str],
    fenced_fields: list[tuple[str, str]],
) -> None:
    """Append full fenced field bodies below the relay table."""
    for label, content in fenced_fields:
        lines.extend(["", f"### {label} (full)", content])


def project_section2_table(
    prose: str,
    *,
    provenance: str,
    fallback_status: str = "partial",
) -> tuple[str, str]:
    """Build normalized §2 table from authored prose.

    Returns ``(body, status)`` with one consistent status across header and table.
    """
    text = strip_machine_tail(prose).strip()
    status = extract_status(text) or status_from_section2(text) or fallback_status

    rows: list[tuple[str, str]] = []
    fenced_appendix: list[tuple[str, str]] = []
    for field, label in SECTION2_FIELDS:
        if field == "status":
            rows.append((label, status))
            continue
        raw = extract_field_section(text, field) or extract_table_field(text, field)
        if raw and looks_fenced(raw):
            rows.append((label, fenced_cell_pointer(provenance)))
            fenced_appendix.append((label, raw))
            continue
        rows.append((label, _extract_cell(text, field, provenance=provenance)))

    lines = [
        "TYPE: CLOSEOUT",
        f"status: {status}",
        f"source_ref: {provenance}",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for field, value in rows:
        lines.append(f"| {field} | {_table_cell(value)} |")
    _append_fenced_sections(lines, fenced_appendix)
    return "\n".join(lines), status


__all__ = [
    "count_relay_parse_miss_fields",
    "count_unclassified_fields",
    "project_section2_table",
]
