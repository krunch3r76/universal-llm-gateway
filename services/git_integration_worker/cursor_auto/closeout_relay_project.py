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
    strip_machine_tail,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
    extract_table_field,
    field_heading_present,
)
from services.git_integration_worker.cursor_auto.lane_a_status import (
    extract_status_claim,
    status_claim_from_section2,
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
_MD_TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<c1>[^|]*?)\s*\|\s*(?P<c2>[^|]*?)\s*(?:\|\s*(?P<c3>[^|]*?)\s*)?\|\s*$",
    re.MULTILINE,
)
_MD_TABLE_SEP_RE = re.compile(r"^\|\s*[-:\s|]+\|\s*$")
_MD_TABLE_HEADER_KEYS = frozenset(
    {"ac", "field", "class", "verdict", "value", "disposition", "evidence"}
)


def _normalize_heading_cell(text: str) -> str:
    return re.sub(r"[*`_]+", "", text.strip()).casefold()


def _is_md_table_header_row(c1: str, c2: str, c3: str | None = None) -> bool:
    h1 = _normalize_heading_cell(c1)
    h2 = _normalize_heading_cell(c2)
    if h1 in _MD_TABLE_HEADER_KEYS and h2 in _MD_TABLE_HEADER_KEYS:
        return True
    if c3 is not None and h1 in _MD_TABLE_HEADER_KEYS and _normalize_heading_cell(c3) in _MD_TABLE_HEADER_KEYS:
        return True
    return False


def _split_md_table_line(line: str) -> list[str]:
    """Split a markdown table row on unescaped pipe boundaries."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner) and inner[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    cells.append("".join(current).strip())
    return cells


def _md_table_to_compact_list(table_text: str) -> str:
    """Render nested markdown table rows as a single-cell ordered list."""
    raw_lines = [
        line.strip()
        for line in table_text.splitlines()
        if line.strip().startswith("|")
    ]
    if len(raw_lines) < 2:
        return table_text.strip()

    header_cells = _split_md_table_line(raw_lines[0])
    column_count = len(header_cells)
    items: list[str] = []
    for line in raw_lines[1:]:
        if _MD_TABLE_SEP_RE.match(line):
            continue
        cells = _split_md_table_line(line)
        if len(cells) < column_count:
            continue
        if _is_md_table_header_row(cells[0], cells[1], cells[2] if len(cells) > 2 else None):
            continue
        if len(cells) > column_count:
            cells = cells[: column_count - 1] + [" | ".join(cells[column_count - 1 :])]
        label = cells[0]
        if column_count == 2:
            items.append(f"{label}: {cells[1]}")
        elif column_count >= 3:
            items.append(f"{label}: {cells[1]} ({cells[2]})")
        else:
            items.append(label)
    return "; ".join(items) if items else table_text.strip()


def normalize_relay_field_value(value: str) -> str:
    """Turn multi-row / nested-table values into a compact list before table escaping."""
    stripped = value.strip()
    if not stripped:
        return stripped
    if "|" in stripped and _MD_TABLE_ROW_RE.search(stripped):
        compact = _md_table_to_compact_list(stripped)
        if compact != stripped.strip():
            return compact
    if "\n" in stripped:
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if len(lines) > 1 and not any(line.startswith("|") for line in lines):
            return "; ".join(lines)
    return stripped


def _render_table_cell(value: str) -> str:
    return _table_cell(normalize_relay_field_value(value))


def count_unclassified_fields(body: str) -> int:
    """Return how many relay cells report §2 parse uncertainty."""
    return len(_UNCLASSIFIED_RE.findall(body))


def count_relay_parse_miss_fields(body: str) -> int:
    """Return how many relay cells honestly report a field the projector could not locate."""
    return len(_RELAY_PARSE_MISS_RE.findall(body))


def _extract_cell(prose: str, field: str, *, provenance: str) -> str:
    if field == "status_claim":
        value = extract_status_claim(prose)
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

    Returns ``(body, status_claim)`` with claim in the ``status_claim`` table row.
    """
    text = strip_machine_tail(prose).strip()
    claim = (
        extract_status_claim(text)
        or status_claim_from_section2(text)
        or fallback_status
    )

    rows: list[tuple[str, str]] = []
    fenced_appendix: list[tuple[str, str]] = []
    for field, label in SECTION2_FIELDS:
        if field == "status_claim":
            rows.append((label, claim))
            continue
        raw = extract_field_section(text, field) or extract_table_field(text, field)
        if raw and looks_fenced(raw):
            rows.append((label, fenced_cell_pointer(provenance)))
            fenced_appendix.append((label, raw))
            continue
        rows.append((label, _extract_cell(text, field, provenance=provenance)))

    lines = [
        "TYPE: CLOSEOUT",
        f"source_ref: {provenance}",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for field, value in rows:
        lines.append(f"| {field} | {_render_table_cell(value)} |")
    _append_fenced_sections(lines, fenced_appendix)
    return "\n".join(lines), claim


__all__ = [
    "count_relay_parse_miss_fields",
    "count_unclassified_fields",
    "normalize_relay_field_value",
    "project_section2_table",
]
