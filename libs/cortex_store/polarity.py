"""Polarity detection for assertion contradiction checking.

Rule-based, no LLM — designed for the hot write path. Detects status antonym
pairs between two claims. Defaults to "no conflict" when ambiguous (false
negatives > false positives).
"""

from __future__ import annotations

import re

_STATUS_ANTONYMS: dict[str, str] = {
    "open": "closed",
    "closed": "open",
    "complete": "incomplete",
    "incomplete": "complete",
    "active": "inactive",
    "inactive": "active",
    "enabled": "disabled",
    "disabled": "enabled",
    "done": "pending",
    "pending": "done",
    "true": "false",
    "false": "true",
    "present": "absent",
    "absent": "present",
    "available": "unavailable",
    "unavailable": "available",
    "valid": "invalid",
    "invalid": "valid",
    "resolved": "unresolved",
    "unresolved": "resolved",
    "blocked": "unblocked",
    "unblocked": "blocked",
}

STOP_WORDS = frozenset(
    {
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "it",
        "its",
        "this",
        "that",
        "than",
        "then",
    }
)

WORD_RE = re.compile(r"\b\w+\b")


def detect_polarity_conflict(claim_a: str, claim_b: str) -> bool:
    """Rule-based polarity check between two claims.

    Returns True iff the claims express opposing status states on the same
    vocabulary (e.g. "open" vs "closed"). Defaults to False when ambiguous —
    false negatives are safer than false positives on the write path.

    The previous negation-asymmetry heuristic was removed (agent-bus thread
    555): rhetorical "not" in ordinary prose combined with trivial ≥3-word
    topicality overlap produced systematic false positives on any entity with
    accumulated history. Semantic polarity detection belongs in an offline /
    post-commit review path with an LLM, not in a word-level rule.
    """
    a_words = set(WORD_RE.findall(claim_a.lower()))
    b_words = set(WORD_RE.findall(claim_b.lower()))

    for word, antonym in _STATUS_ANTONYMS.items():
        if word in a_words and antonym in b_words:
            return True

    return False


def build_candidate_query(text: str) -> str:
    """Build FTS5 OR query from significant words for broad candidate recall."""
    words = WORD_RE.findall(text.lower())
    significant = [w for w in words if len(w) > 2 and w not in STOP_WORDS][:10]
    if not significant:
        return ""
    return " OR ".join(f'"{w}"' for w in significant)
