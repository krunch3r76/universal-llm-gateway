"""Mutation summaries for markdown section write ops (md_replace, md_delete)."""

from __future__ import annotations

from typing import Any

_SHRINK_WARN_RATIO = 0.5
_MIN_PRIOR_CHARS_FOR_WARN = 80


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def section_mutation_summary(prior_body: str, new_body: str) -> dict[str, Any]:
    """Line/char delta for a section body before vs after a write."""
    prior_chars = len(prior_body)
    new_chars = len(new_body)
    prior_lines = _line_count(prior_body)
    new_lines = _line_count(new_body)
    summary: dict[str, Any] = {
        "prior_body_chars": prior_chars,
        "new_body_chars": new_chars,
        "prior_body_lines": prior_lines,
        "new_body_lines": new_lines,
        "lines_removed": max(0, prior_lines - new_lines),
        "lines_added": max(0, new_lines - prior_lines),
    }
    if prior_chars > 0:
        summary["size_delta_ratio"] = round((new_chars - prior_chars) / prior_chars, 4)
    return summary


def delete_mutation_summary(prior_body: str) -> dict[str, Any]:
    """Summary when an entire section body is removed."""
    prior_chars = len(prior_body)
    prior_lines = _line_count(prior_body)
    return {
        "prior_body_chars": prior_chars,
        "prior_body_lines": prior_lines,
        "deleted_body_chars": prior_chars,
        "deleted_body_lines": prior_lines,
        "new_body_chars": 0,
        "new_body_lines": 0,
        "lines_removed": prior_lines,
        "lines_added": 0,
        "size_delta_ratio": -1.0 if prior_chars else 0.0,
    }


def shrink_warning(prior_body: str, new_body: str, *, op: str) -> str | None:
    """Advisory when md_rewrite_section drops most of a section body."""
    prior_chars = len(prior_body)
    if prior_chars < _MIN_PRIOR_CHARS_FOR_WARN:
        return None
    new_chars = len(new_body)
    if new_chars / prior_chars >= (1.0 - _SHRINK_WARN_RATIO):
        return None
    removed_lines = max(0, _line_count(prior_body) - _line_count(new_body))
    pct = 100 - int((new_chars / prior_chars) * 100)
    return (
        f"{op} shrunk section body by ~{pct}% "
        f"({prior_chars} → {new_chars} chars, ~{removed_lines} lines removed). "
        "Re-read with md_read if the rewrite was unintended."
    )


def direct_child_headings(text: str, section_path: str) -> list[str]:
    """Direct child headings of ``section_path`` in document order."""
    from markdown_sections import SectionError, parse_sections, resolve_section

    try:
        parent = resolve_section(text, section_path)
    except SectionError:
        return []
    if parent.level <= 0:
        return []
    headings: list[str] = []
    for sec in parse_sections(text):
        if sec.level != parent.level + 1:
            continue
        if sec.start <= parent.start or sec.start >= parent.end:
            continue
        headings.append(sec.heading)
    return headings


def removed_child_headings(before: str, after: str, section_path: str) -> list[str]:
    """Headings present under ``section_path`` before a write and absent after."""
    after_set = set(direct_child_headings(after, section_path))
    return [heading for heading in direct_child_headings(before, section_path) if heading not in after_set]


def delete_warning(prior_body: str) -> str | None:
    """Warn when deleting a non-trivial section body."""
    prior_chars = len(prior_body)
    if prior_chars < _MIN_PRIOR_CHARS_FOR_WARN:
        return None
    prior_lines = _line_count(prior_body)
    return (
        f"md_delete removed the entire section body "
        f"({prior_chars} chars, {prior_lines} lines). "
        "Re-read with md_read before delete if you only meant to trim content."
    )
