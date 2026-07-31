"""Heading / bold-field extraction for CLOSEOUT judgment cells."""

from __future__ import annotations

import re

_FIELD_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "status": ("status",),
    "ac_verdict": (
        "ac_verdict",
        "ac verdict",
        "verdict",
        "ac1 per-site disposition",
        "ac1",
    ),
    "deltas_to_spec": ("deltas_to_spec", "deltas to spec", "delta to spec", "scope delta", "scope_delta"),
    "decisions_taken": ("decisions_taken", "decisions taken"),
    "next": ("next", "next steps"),
    "open forks": ("open_forks", "open forks"),
    "effects": ("effects",),
    "evidence": ("evidence",),
    "access": ("access",),
    "coverage": ("coverage",),
    "model_actual": ("model actual", "model_actual", "modelactual"),
}

_TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<field>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$",
    re.MULTILINE,
)
_TABLE_SEP_RE = re.compile(r"^\|\s*[-:]+\s*\|")

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_FIELD_LINE_RE = re.compile(
    r"(?im)^\*\*(?P<heading>[^*\n]+?):\*\*\s*(?P<rest>.*)$",
)
_BOLD_HEADING_ONLY_RE = re.compile(
    r"(?im)^\*\*(?P<heading>[^*\n]+?)\*\*\s*$",
)


def _normalize_heading_key(text: str) -> str:
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = re.sub(r"[*`_]+", "", text)
    return re.sub(r"[_\-\s]+", "", text.casefold())


def _heading_matches_field(heading: str, field: str) -> bool:
    normalized_heading = _normalize_heading_key(heading)
    for alias in _FIELD_HEADING_ALIASES[field]:
        normalized_alias = _normalize_heading_key(alias)
        if normalized_heading == normalized_alias or normalized_heading.startswith(
            normalized_alias
        ):
            return True
    return False


def _table_heading_matches_field(heading: str, field: str) -> bool:
    """Strict table-row field match — reject AC1-style verdict rows."""
    normalized_heading = _normalize_heading_key(heading)
    if re.fullmatch(r"ac\d+", normalized_heading):
        return False
    if normalized_heading == _normalize_heading_key(field):
        return True
    for alias in _FIELD_HEADING_ALIASES[field]:
        normalized_alias = _normalize_heading_key(alias)
        if normalized_alias == "ac1":
            continue
        if normalized_heading == normalized_alias:
            return True
    return False


def _extract_atx_section(body: str, field: str) -> str | None:
    matches = list(_ATX_HEADING_RE.finditer(body))
    for index, match in enumerate(matches):
        heading = match.group(2).strip()
        if not _heading_matches_field(heading, field):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section = body[start:end].strip()
        return section or None
    return None


def _normalize_field_heading(raw: str) -> str:
    return raw.strip().rstrip(":").strip()


def field_heading_present(body: str, field: str) -> bool:
    """True when a §2 field heading exists in authored prose."""
    for _start, _end, heading, _rest in _field_line_spans(body):
        if _heading_matches_field(heading, field):
            return True
    for match in _ATX_HEADING_RE.finditer(body):
        if _heading_matches_field(match.group(2).strip(), field):
            return True
    return extract_table_field(body, field) is not None


def _field_line_spans(body: str) -> list[tuple[int, int, str, str]]:
    """Collect bold §2 field lines — ``**field:** rest`` and ``**field**`` heading-only."""
    spans: list[tuple[int, int, str, str]] = []
    for match in _BOLD_FIELD_LINE_RE.finditer(body):
        heading = _normalize_field_heading(match.group("heading"))
        rest = match.group("rest").strip()
        spans.append((match.start(), match.end(), heading, rest))
    colon_starts = {start for start, _, _, _ in spans}
    for match in _BOLD_HEADING_ONLY_RE.finditer(body):
        if match.start() in colon_starts:
            continue
        heading = _normalize_field_heading(match.group("heading"))
        spans.append((match.start(), match.end(), heading, ""))
    spans.sort(key=lambda item: item[0])
    return spans


def _next_field_boundary(body: str, start: int) -> int:
    for line_start, _, _, _ in _field_line_spans(body):
        if line_start >= start:
            return line_start
    for match in _ATX_HEADING_RE.finditer(body):
        if match.start() >= start:
            return match.start()
    return len(body)


def _extract_bold_same_line(body: str, field: str) -> str | None:
    for _start, _end, heading, rest in _field_line_spans(body):
        if _heading_matches_field(heading, field) and rest:
            return rest
    return None


def _extract_bold_section(body: str, field: str) -> str | None:
    lines = body.splitlines(keepends=True)
    for index, line in enumerate(lines):
        colon_match = _BOLD_FIELD_LINE_RE.match(line)
        heading_only_match = (
            None if colon_match else _BOLD_HEADING_ONLY_RE.match(line)
        )
        match = colon_match or heading_only_match
        if match is None:
            continue
        heading = _normalize_field_heading(match.group("heading"))
        if not _heading_matches_field(heading, field):
            continue
        rest = match.group("rest").strip() if colon_match else ""
        if rest:
            return rest
        start = sum(len(lines[i]) for i in range(index + 1))
        end = _next_field_boundary(body, start)
        section = body[start:end].strip()
        return section or None
    return None


def extract_table_field(body: str, field: str) -> str | None:
    """Extract a field value from an existing markdown table row."""
    for match in _TABLE_ROW_RE.finditer(body):
        row_field = match.group("field").strip()
        if _TABLE_SEP_RE.match(f"|{row_field}|"):
            continue
        if _table_heading_matches_field(row_field, field):
            value = match.group("value").strip()
            return value or None
    return None


def extract_status(body: str) -> str | None:
    """Extract closeout status from header line, table row, or bold field."""
    header = re.search(
        r"(?im)^status\s*[:=]\s*`?(complete|partial|blocked)`?",
        body,
    )
    if header:
        return header.group(1).lower()
    table = extract_table_field(body, "status")
    if table:
        normalized = table.strip().lower().strip("`")
        if normalized in {"complete", "partial", "blocked"}:
            return normalized
    bold = _extract_bold_same_line(body, "status") or _extract_bold_section(body, "status")
    if bold:
        normalized = bold.strip().lower().strip("`")
        if normalized in {"complete", "partial", "blocked"}:
            return normalized
    return status_from_section2(body)


def status_from_section2(text: str) -> str | None:
    """Extract ``complete|partial|blocked`` from authored §2 prose, if present."""
    match = re.search(
        r"(?im)^(?:\*\*)?status(?:\*\*)?\s*[:=]\s*`?(complete|partial|blocked)`?",
        text,
    )
    if match is None:
        return None
    return match.group(1).lower()


def extract_field_section(body: str, field: str) -> str | None:
    """Extract judgment-cell text for *field* from cortex prose headings."""
    table = extract_table_field(body, field)
    if table:
        return table
    same_line = _extract_bold_same_line(body, field)
    if same_line:
        return same_line
    for extractor in (_extract_bold_section, _extract_atx_section):
        section = extractor(body, field)
        if section:
            return section
    return None


__all__ = [
    "extract_field_section",
    "extract_status",
    "extract_table_field",
    "field_heading_present",
    "status_from_section2",
]
