"""Prepend impresario skill Use-line on Glass spawns when slug or title signals impresario."""

from __future__ import annotations

import re

IMPRESARIO_SKILL_USE_LINE = "Use the impresario skill."

_IMPRESARIO_SLUG_SEGMENT = re.compile(r"(?:^|-)impresario(?:-|$)", re.IGNORECASE)
_REVIEWISH_SLUG_SEGMENT = re.compile(r"(?:^|-)review(?:er)?(?:-|$)", re.IGNORECASE)
_IMPRESARIO_TITLE = re.compile(r"\bimpresario\b", re.IGNORECASE)
_REVIEWISH_TITLE = re.compile(r"\b(?:review|reviewer)\b", re.IGNORECASE)


def _signals_impresario(*, slug: str | None, title: str | None) -> bool:
    slug_text = str(slug or "").strip().replace("_", "-")
    if slug_text and not _REVIEWISH_SLUG_SEGMENT.search(slug_text):
        if _IMPRESARIO_SLUG_SEGMENT.search(slug_text):
            return True
    title_text = str(title or "").strip()
    if title_text and not _REVIEWISH_TITLE.search(title_text):
        if _IMPRESARIO_TITLE.search(title_text):
            return True
    return False


def augment_spawn_prompt_for_impresario(
    prompt: str,
    *,
    slug: str | None = None,
    title: str | None = None,
) -> str:
    """Idempotently prepend the impresario Use-line when slug or title matches."""
    body = str(prompt or "").strip()
    if not body:
        return body
    if IMPRESARIO_SKILL_USE_LINE.casefold() in body.casefold():
        return body
    if not _signals_impresario(slug=slug, title=title):
        return body
    return f"{IMPRESARIO_SKILL_USE_LINE}\n\n{body}"


__all__ = [
    "IMPRESARIO_SKILL_USE_LINE",
    "augment_spawn_prompt_for_impresario",
]
