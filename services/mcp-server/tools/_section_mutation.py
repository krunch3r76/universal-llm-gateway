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
    """Warn when a replace shrinks the section body by more than half."""
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
        "These ops replace the ENTIRE section body — for additive edits use "
        "md_append or md_insert. Re-read with md_read if truncation was unintended."
    )


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
