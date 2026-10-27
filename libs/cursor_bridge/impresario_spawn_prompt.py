"""Prepend impresario skill Use-line on Glass spawns when slug or title signals impresario."""

from __future__ import annotations

IMPRESARIO_SKILL_USE_LINE = "Use the impresario skill."


def _signals_impresario(*, slug: str | None, title: str | None) -> bool:
    for part in (slug, title):
        text = str(part or "").strip()
        if text and "impresario" in text.casefold():
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
