"""Heading / bold-field extraction for CLOSEOUT judgment cells."""

from __future__ import annotations

import re

_FIELD_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "ac_verdict": (
        "ac_verdict",
        "ac verdict",
        "verdict",
        "ac1 per-site disposition",
        "ac1",
    ),
    "deltas_to_spec": ("deltas_to_spec", "deltas to spec", "delta to spec"),
    "decisions_taken": ("decisions_taken", "decisions taken"),
    "next": ("next", "next steps"),
    "open forks": ("open_forks", "open forks"),
    "effects": ("effects",),
}

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_FIELD_RE = re.compile(
    r"(?im)^\*\*(?P<heading>[^*\n]+?)\*\*\s*:?\s*\n(?P<body>(?:(?!\*\*[^*\n]+\*\*).)+)"
)


def _normalize_heading_key(text: str) -> str:
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


def _extract_bold_section(body: str, field: str) -> str | None:
    for match in _BOLD_FIELD_RE.finditer(body):
        heading = match.group("heading").strip()
        if not _heading_matches_field(heading, field):
            continue
        section = match.group("body").strip()
        return section or None
    return None


def extract_field_section(body: str, field: str) -> str | None:
    """Extract judgment-cell text for *field* from cortex prose headings."""
    for extractor in (_extract_bold_section, _extract_atx_section):
        section = extractor(body, field)
        if section:
            return section
    return None


__all__ = ["extract_field_section"]
