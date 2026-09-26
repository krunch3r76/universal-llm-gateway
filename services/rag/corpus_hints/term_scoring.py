"""Term noise filtering and IDF-style scoring for corpus hint selection.

Pure functions used by ``update_corpus_hints`` to rank candidate name/topic
terms from the property index: ``is_structural_noise`` rejects paths, URLs,
math variables, citations and doc-structure refs; ``score_term`` gives a
hybrid IDF plus chunk-density score; ``entity_shape_boost`` multiplies that
score to favour hyphenated and single-token entity-like names.
"""

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
    """Return a score multiplier favouring entity-shaped corpus hint terms.

    Hyphenated terms (e.g. product or model names) get ``hyphen_boost``;
    otherwise single-token terms get ``single_token_boost``; multi-word
    phrases get 1.0. Applied on top of ``score_term`` in update_corpus_hints.
    """
    if "-" in term:
        return hyphen_boost
    if " " not in term:
        return single_token_boost
    return 1.0


def score_term(chunk_count: int, doc_count: int, total_docs: int) -> float:
    """Compute a hybrid IDF plus chunk-density score for a candidate hint term.

    Score is ``log(total_docs / doc_count) + 0.3 * log(1 + chunk_count /
    doc_count)``, so rarer documents dominate and repeated mentions per
    document add a small boost. When ``doc_count`` is 0 (no document stats)
    it falls back to ``log(1 + chunk_count)``, or 0.0 with no chunks.
    """
    if doc_count == 0:
        return math.log(1 + chunk_count) if chunk_count > 0 else 0.0
    idf = math.log(total_docs / doc_count)
    chunk_boost = math.log(1 + chunk_count / doc_count) * 0.3
    return idf + chunk_boost
