"""Pure helpers for per-skill vocabulary attribution JOIN."""

from __future__ import annotations

from collections.abc import Callable


def build_skill_vocabulary_rows(
    *,
    scope_vocabulary: list[tuple[str, str]],
    source_term_counts: list[tuple[str, str, int, int]],
    corpus_hint_scores: dict[str, float],
    slug_from_source: Callable[[str | None], str | None],
) -> list[tuple[str, str, str, float, int]]:
    """JOIN classified (register, term) × per-source occurrences → skill rows."""
    term_to_register: dict[str, tuple[str, str]] = {}
    for register, term in scope_vocabulary:
        normalized = term.strip()
        if normalized:
            term_to_register[normalized.lower()] = (register, normalized)

    merged: dict[tuple[str, str, str], tuple[float, int]] = {}
    for source, term, chunk_count, _doc_count in source_term_counts:
        lookup = term_to_register.get(str(term).lower())
        if lookup is None:
            continue
        register, canonical_term = lookup
        slug = slug_from_source(source)
        if not slug:
            continue
        score = corpus_hint_scores.get(canonical_term)
        if score is None:
            score = corpus_hint_scores.get(term)
        if score is None:
            score = float(chunk_count)
        key = (slug, register, canonical_term)
        prev_score, prev_chunks = merged.get(key, (0.0, 0))
        merged[key] = (max(prev_score, score), prev_chunks + int(chunk_count))

    return [
        (slug, register, term, score, chunk_count)
        for (slug, register, term), (score, chunk_count) in sorted(merged.items())
    ]
