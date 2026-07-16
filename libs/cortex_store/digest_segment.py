"""SEGMENT helper for journal-digest — split dated entries by H1 headings."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from markdown_sections import Section, parse_sections

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True, kw_only=True)
class Segment:
    entry_anchor: str
    heading: str
    entry_text: str


def heading_to_slug(heading: str) -> str:
    """Lowercase kebab slug: non-alnum runs collapse to a single hyphen."""
    slug = _SLUG_RE.sub("-", heading.lower()).strip("-")
    return re.sub(r"-+", "-", slug)


def _section_entry_text(text: str, sec: Section) -> str:
    if sec.level == 0:
        return text[sec.start : sec.end]
    lines = text.splitlines(keepends=True)
    line_start = sum(len(lines[i]) for i in range(sec.line - 1))
    return text[line_start : sec.end]


def _unique_anchor(entry_date: str, slug: str, seen: dict[str, int]) -> str:
    """First occurrence ``{date}#{slug}``; later dups ``{date}#{slug}-2``, ``-3``, …"""
    count = seen.get(slug, 0) + 1
    seen[slug] = count
    if count == 1:
        return f"{entry_date}#{slug}"
    return f"{entry_date}#{slug}-{count}"


def segment_journal_entry(text: str, *, entry_date: str) -> list[Segment]:
    """Split *text* into H1 segments with ``{entry_date}#{slug}`` anchors.

    Duplicate H1 headings get a deterministic suffix (``#repeat``, ``#repeat-2``)
    so per-section watermark identity stays unique (Sol F3).
    """
    sections = parse_sections(text)
    segments: list[Segment] = []
    seen_slugs: dict[str, int] = {}

    preamble = next((s for s in sections if s.level == 0), None)
    if preamble is not None:
        preamble_text = text[preamble.start : preamble.end]
        if preamble_text.strip():
            segments.append(
                Segment(
                    entry_anchor=f"{entry_date}#preamble",
                    heading="",
                    entry_text=preamble_text,
                )
            )

    for sec in (s for s in sections if s.level == 1):
        slug = heading_to_slug(sec.heading) or "section"
        segments.append(
            Segment(
                entry_anchor=_unique_anchor(entry_date, slug, seen_slugs),
                heading=sec.heading,
                entry_text=_section_entry_text(text, sec),
            )
        )

    return segments


def aggregate_auto_segment_digest(
    digest_fn: Callable[..., dict[str, Any]],
    *,
    journal_entity_id: str,
    entry_text: str,
    entry_date: str,
    journal_uri: str | None = None,
) -> dict[str, Any]:
    """Run *digest_fn* per segment; collect per-section results without fail-fast."""
    segments = segment_journal_entry(entry_text, entry_date=entry_date)
    sections: list[dict[str, Any]] = []
    summary: dict[str, int] = {}

    for seg in segments:
        result = digest_fn(
            journal_entity_id=journal_entity_id,
            entry_anchor=seg.entry_anchor,
            entry_text=seg.entry_text,
            journal_uri=journal_uri,
        )
        row = {"entry_anchor": seg.entry_anchor, **result}
        sections.append(row)
        status = str(result.get("status") or ("error" if "error" in result else "unknown"))
        summary[status] = summary.get(status, 0) + 1

    return {
        "status": "segmented",
        "entry_date": entry_date,
        "sections": sections,
        "summary": summary,
    }
