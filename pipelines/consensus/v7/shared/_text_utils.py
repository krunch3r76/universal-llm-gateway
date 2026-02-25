"""
Text access utilities for statement processing.

Provides resolution-aware text access following the progressive enrichment
pattern: pronoun resolution adds resolution data without mutating `text`.

Invariant: get_statement_text(stmt) returns non-empty if stmt is valid
"""

from __future__ import annotations


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
