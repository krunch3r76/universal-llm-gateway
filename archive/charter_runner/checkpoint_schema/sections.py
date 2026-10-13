"""Markdown section helpers extracted from checkpoint_parse.

Shared by checkpoint parsing, closeout aggregation, and plain-language
``## What happened (plain)`` extraction at arc close.
"""

from __future__ import annotations

import re

_PLAIN_HEADING = "what happened (plain)"


def split_sections(body: str) -> dict[str, str]:
    """Split a markdown body into ``## heading`` -> section-text (lowercased key)."""
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    sections[current] = []
    for line in (body or "").splitlines():
        heading = re.match(r"^#{2,}\s+(.*?)\s*$", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def find_section(sections: dict[str, str], *needles: str) -> str:
    """Return the first section whose heading contains any needle."""
    for key, text in sections.items():
        if any(n in key for n in needles):
            return text
    return ""


def extract_what_happened_plain(body: str) -> str | None:
    """Return ``## What happened (plain)`` prose when present."""
    sections = split_sections(body or "")
    text = find_section(sections, _PLAIN_HEADING)
    if not text:
        return None
    stripped = text.strip()
    return stripped or None


def aggregate_what_happened_plain(checkpoint_bodies: list[str]) -> str:
    """Concatenate per-window plain sections in checkpoint order."""
    parts: list[str] = []
    for body in checkpoint_bodies:
        plain = extract_what_happened_plain(body)
        if plain:
            parts.append(plain.strip())
    if not parts:
        return "_No plain window summaries were recorded this arc._"
    return "\n\n".join(parts)


def extract_remaining_work(body: str) -> str:
    """Plain-language snapshot of final Next-pickup + BLOCKED from latest CHECKPOINT."""
    sections = split_sections(body or "")
    next_text = find_section(sections, "next pickup", "next-pickup")
    blocked_text = find_section(sections, "blocked")
    lines: list[str] = []
    if next_text.strip():
        lines.append("**Next pickup:**")
        lines.append(next_text.strip())
    if blocked_text.strip() and "none" not in blocked_text.lower()[:20]:
        lines.append("**Blocked:**")
        lines.append(blocked_text.strip())
    if not lines:
        return "_Arc concluded with no remaining gated pickup on the final CHECKPOINT._"
    return "\n\n".join(lines)


__all__ = [
    "aggregate_what_happened_plain",
    "extract_remaining_work",
    "extract_what_happened_plain",
    "find_section",
    "split_sections",
]
