"""Size-gated CDP ``<skills_inline>`` excerpts (friction a:27142).

Doctrine: non-Claude / ``cursor_only`` skills seal as **excerpts**, not full
``SKILL.md`` bodies. Full SOT remains on disk; sealed Cowork context stays lean.
"""

from __future__ import annotations

CDP_INLINE_SKILL_MAX_CHARS = 6000

_TRUNCATION_MARKER = (
    "… [truncated CDP inline excerpt for {slug}; full SOT not sealed]"
)


def excerpt_skill_body(
    body: str,
    *,
    slug: str,
    max_chars: int = CDP_INLINE_SKILL_MAX_CHARS,
) -> str:
    """Return a size-gated excerpt of a skill body for CDP inline seal.

    Preserves YAML frontmatter when present. Truncates the remainder so the
    whole result (including marker) is at most ``max_chars``.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    text = body if body.endswith("\n") else f"{body}\n"
    if len(text) <= max_chars:
        return text

    marker = _TRUNCATION_MARKER.format(slug=slug)
    # Reserve room for newline + marker.
    budget = max_chars - len(marker) - 1
    if budget < 64:
        return f"{marker}\n"

    head = text[:budget]
    frontmatter_end = _yaml_frontmatter_end(text)
    if frontmatter_end is not None and frontmatter_end < budget:
        # Prefer cutting after frontmatter so metadata stays intact.
        cut = head.rfind("\n")
        if cut > frontmatter_end:
            head = head[:cut]
    else:
        cut = head.rfind("\n")
        if cut >= 64:
            head = head[:cut]

    return f"{head.rstrip()}\n{marker}\n"


def _yaml_frontmatter_end(text: str) -> int | None:
    """Return index after closing ``---`` of YAML frontmatter, or None."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None
    # Skip opening fence.
    start = 4 if text.startswith("---\n") else 5
    close = text.find("\n---\n", start)
    if close < 0:
        close = text.find("\n---\r\n", start)
        if close < 0:
            return None
        return close + len("\n---\r\n")
    return close + len("\n---\n")
