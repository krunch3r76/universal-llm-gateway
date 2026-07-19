"""Term noise filtering and IDF-style scoring for corpus hint selection."""

from __future__ import annotations

import math

from services.rag.corpus_hints.constants import (
    AUTHOR_CITATION_RE,
    DOCUMENT_STRUCTURE_RE,
    GREEK_SINGLE_RE,
    MATH_VARIABLE_RE,
    MIN_TERM_LENGTH,
)

__all__ = ["entity_shape_boost", "is_structural_noise", "score_term"]


def is_structural_noise(term: str) -> bool:
    """Reject terms that are file paths, URLs, math notation, or doc refs."""
    t = term.strip()
    if len(t) < MIN_TERM_LENGTH:
        return True
    if t.startswith(("/", "http://", "https://", "./", "../")):
        return True
    if "/" in t and not any(c.isalpha() for c in t.split("/")[0]):
        return True
    if DOCUMENT_STRUCTURE_RE.match(t):
        return True
    if MATH_VARIABLE_RE.match(t):
        return True
    if AUTHOR_CITATION_RE.search(t):
        return True
    if GREEK_SINGLE_RE.match(t):
        return True
    return False


def entity_shape_boost(
    term: str,
    *,
    hyphen_boost: float = 1.3,
    single_token_boost: float = 1.2,
) -> float:
    """Return a multiplicative boost based on term shape."""
    if "-" in term:
        return hyphen_boost
    if " " not in term:
        return single_token_boost
    return 1.0


def score_term(chunk_count: int, doc_count: int, total_docs: int) -> float:
    """Hybrid IDF + chunk-boost score using document frequency."""
    if doc_count == 0:
        return math.log(1 + chunk_count) if chunk_count > 0 else 0.0
    idf = math.log(total_docs / doc_count)
    chunk_boost = math.log(1 + chunk_count / doc_count) * 0.3
    return idf + chunk_boost
