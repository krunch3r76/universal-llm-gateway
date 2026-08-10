"""Lane-A §2 status claim extraction — distinct from envelope measurement ``status:``."""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
    extract_table_field,
    fenced_spans,
    in_fenced_span,
)

_VALID_STATUS_CLAIMS = frozenset({"complete", "partial", "blocked"})
_STATUS_CLAIM_LINE_RE = re.compile(
    r"(?im)^status_claim:\s*`?(complete|partial|blocked)`?"
)
_BOLD_STATUS_CLAIM_RE = re.compile(
    r"(?im)^\*\*status_claim:\*\*\s*`?(complete|partial|blocked)`?"
)
_LEGACY_STATUS_LINE_RE = re.compile(
    r"(?im)^status:\s*`?(complete|partial|blocked)`?"
)
_BOLD_LEGACY_STATUS_RE = re.compile(
    r"(?im)^\*\*status:\*\*\s*`?(complete|partial|blocked)`?"
)


def normalize_status_claim_value(raw: str) -> str | None:
    """Extract ``complete|partial|blocked`` from a status_claim cell value."""
    text = raw.strip().strip("`").strip()
    if text.casefold().startswith("status_claim:"):
        text = text.split(":", 1)[1].strip()
    match = re.match(r"^(complete|partial|blocked)\b", text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    normalized = text.casefold()
    if normalized in _VALID_STATUS_CLAIMS:
        return normalized
    return None


def _extract_status_claim_field(
    text: str,
    field: str,
    *,
    include_legacy_status_line: bool,
) -> str | None:
    """Return normalized status claim from §2 *field* prose."""
    fenced = fenced_spans(text)
    line_patterns: list[re.Pattern[str]] = []
    bold_patterns: list[re.Pattern[str]] = []

    if field == "status_claim":
        line_patterns.append(_STATUS_CLAIM_LINE_RE)
        bold_patterns.append(_BOLD_STATUS_CLAIM_RE)
        if include_legacy_status_line:
            line_patterns.extend((_LEGACY_STATUS_LINE_RE,))
            bold_patterns.extend((_BOLD_LEGACY_STATUS_RE,))
    elif field == "status" and include_legacy_status_line:
        line_patterns.append(_LEGACY_STATUS_LINE_RE)
        bold_patterns.append(_BOLD_LEGACY_STATUS_RE)

    for pattern in line_patterns:
        for match in pattern.finditer(text):
            if not in_fenced_span(fenced, match.start()):
                return match.group(1).lower()

    for bold_re in bold_patterns:
        for match in bold_re.finditer(text):
            if not in_fenced_span(fenced, match.start()):
                return match.group(1).lower()

    section = extract_field_section(text, field)
    if section and section.strip():
        normalized = normalize_status_claim_value(section.strip())
        if normalized is not None:
            return normalized

    table = extract_table_field(text, field)
    if table:
        normalized = normalize_status_claim_value(table)
        if normalized is not None:
            return normalized
    return None


def extract_status_claim(body: str) -> str | None:
    """Return agent §2 status claim — never envelope ``status:`` measurement line."""
    claim = _extract_status_claim_field(
        body or "",
        "status_claim",
        include_legacy_status_line=False,
    )
    if claim is not None:
        return claim
    return _extract_status_claim_field(
        body or "",
        "status",
        include_legacy_status_line=True,
    )


def status_claim_from_section2(text: str) -> str | None:
    """Extract claim token from authored §2 prose headings."""
    for pattern in (
        r"(?im)^(?:\*\*)?status_claim(?:\*\*)?\s*[:=]\s*`?(complete|partial|blocked)`?",
        r"(?im)^(?:\*\*)?status(?:\*\*)?\s*[:=]\s*`?(complete|partial|blocked)`?",
    ):
        match = re.search(pattern, text)
        if match is not None:
            return match.group(1).lower()
    return None


__all__ = [
    "extract_status_claim",
    "normalize_status_claim_value",
    "status_claim_from_section2",
]
