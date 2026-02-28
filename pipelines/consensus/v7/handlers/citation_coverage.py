"""
Programmatic assess handler: citation coverage audit.

Parses the model's structured output (prose + DROPPED FACTS section) to produce
three categorized lists for debugging:
- incorporated: fact indices cited inline in the prose
- excluded_with_reason: facts the model listed under DROPPED FACTS with reasoning
- excluded_without_reason: facts absent from both (silent drops)

Always accepts. The handler also strips the DROPPED FACTS section and returns
the prose-only text as the artifact for downstream consumers.
"""

from __future__ import annotations

import re
from typing import Any

from pipeline_assess_registry import register_assess_handler

_BRACKET_RE = re.compile(r"\[[\d,\s]+\]")
_DROPPED_SPLIT_RE = re.compile(r"\n?DROPPED FACTS:\s*")
_DROPPED_LINE_RE = re.compile(r"^\[(\d+)\]\s*(.+)", re.MULTILINE)


def _extract_indices(text: str) -> set[int]:
    """Extract fact indices from [N] or [N, M, ...] bracket citations."""
    indices: set[int] = set()
    for bracket in _BRACKET_RE.findall(text):
        indices.update(int(n) for n in re.findall(r"\d+", bracket))
    return indices


def _extract_prose(text: str) -> str:
    """Return only the prose portion (everything before DROPPED FACTS)."""
    parts = _DROPPED_SPLIT_RE.split(text, maxsplit=1)
    return parts[0].rstrip()


def _parse_dropped_section(text: str) -> dict[int, str]:
    """Parse DROPPED FACTS section into {index: reason}."""
    parts = _DROPPED_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) < 2:
        return {}
    dropped_text = parts[1]
    if dropped_text.strip().lower() == "none":
        return {}
    return {
        int(m.group(1)): m.group(2).strip()
        for m in _DROPPED_LINE_RE.finditer(dropped_text)
    }


def citation_coverage_check(resolved: dict[str, Any]) -> dict[str, Any]:
    """
    Audit citation coverage — always accepts.

    Parses the model response into prose + dropped section, then categorizes
    every expected fact index into one of three lists. Returns the stripped
    prose as 'artifact' so the loop stores it without the DROPPED FACTS block.
    """
    raw_artifact: str = resolved.get("artifact", "")
    verified_facts: str = str(resolved.get("verified_facts", ""))

    expected = {
        int(m) for m in re.findall(r"^\[(\d+)\]", verified_facts, re.MULTILINE)
    }

    prose = _extract_prose(raw_artifact)
    incorporated = sorted(expected & _extract_indices(prose))
    dropped = _parse_dropped_section(raw_artifact)
    excluded_with_reason = {
        idx: reason for idx, reason in dropped.items() if idx in expected
    }
    excluded_without_reason = sorted(
        expected - set(incorporated) - set(excluded_with_reason)
    )

    return {
        "action": "accept",
        "artifact": prose,
        "incorporated": incorporated,
        "excluded_with_reason": excluded_with_reason,
        "excluded_without_reason": excluded_without_reason,
        "reason": (
            f"Incorporated: {len(incorporated)}, "
            f"excluded (reasoned): {len(excluded_with_reason)}, "
            f"excluded (silent): {len(excluded_without_reason)}"
        ),
    }


register_assess_handler("citation_coverage_check", citation_coverage_check)
