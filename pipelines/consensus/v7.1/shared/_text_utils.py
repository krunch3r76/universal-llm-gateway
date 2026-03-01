"""
Text access utilities for statement processing.

Provides resolution-aware text access following the progressive enrichment
pattern: pronoun resolution adds resolution data without mutating `text`.

Invariant: get_statement_text(stmt) returns non-empty if stmt is valid
"""

from __future__ import annotations

import re

_FACT_CITATION_PATTERN: re.Pattern[str] = re.compile(
    r"\[(?:Fact\s+)?\d+(?:\s*,\s*(?:Fact\s+)?\d+)*\]"
)
_SPACE_BEFORE_PUNCT_PATTERN: re.Pattern[str] = re.compile(r"\s+([.,;:!?])")
_MULTI_SPACE: re.Pattern[str] = re.compile(r" {2,}")


def get_statement_text(stmt: dict, *, prefer_resolved: bool = True) -> str:
    """
    Get the appropriate text from a statement.

    Args:
        stmt: Statement dict (has `resolution.text` or `text`)
        prefer_resolved: If True, return resolved text if present, else `text`.

    Returns:
        The appropriate text string, or empty string if neither field present.

    Resolution priority:
        1. stmt["resolution"]["text"] (structured)
        2. stmt["text"] (original)
    """
    if prefer_resolved:
        resolution = stmt.get("resolution")
        if resolution and isinstance(resolution, dict):
            text = resolution.get("text")
            if text:
                return text
        return stmt.get("text", "")
    else:
        return stmt.get("text", "")


def strip_fact_citations(text: str) -> str:
    """Remove all [Fact N] / [Fact N, M] citation tags from text.

    Tags are load-bearing during synthesis (citation filter in combine_passages)
    but must be absent from final user-facing output.
    """
    stripped = _FACT_CITATION_PATTERN.sub("", text)
    stripped = _SPACE_BEFORE_PUNCT_PATTERN.sub(r"\1", stripped)
    stripped = _MULTI_SPACE.sub(" ", stripped)
    return stripped.strip()
