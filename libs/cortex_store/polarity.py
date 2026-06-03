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

# A genuine status contradiction is short-vs-short: "X is enabled" vs
# "X is disabled". Long decision/friction prose accumulates generic status
# tokens (resolved/open/done/...) incidentally, with no subject alignment —
# the bag-of-words antonym match then fires on unrelated senses (e.g. a
# decision "resolved" vs an extraction "unresolved", agent-bus:1197). Since
# the antonym rule has no subject/predicate parse, restrict it to claims short
# enough to *be* a status assertion. Above this significant-word count a claim
# is free-text prose: skip the rule (consistent with the false-negative-safe
# bias and thread 555's removal of the negation-asymmetry twin).
_MAX_STATUS_CLAIM_WORDS = 10


def _significant_word_count(words: set[str]) -> int:
    """Count content words (length > 2, not a stop word) in a claim."""
    return sum(1 for w in words if len(w) > 2 and w not in STOP_WORDS)


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

    The bag-of-words antonym match is scoped to short status-like claims
    (≤ ``_MAX_STATUS_CLAIM_WORDS`` significant words each). Long decision /
    friction prose accrues generic status tokens incidentally and tripped
    false 409s on every active decision entity (agent-bus thread 1197); since
    the rule has no subject alignment, it must not run against free-text prose.
    """
    a_words = set(WORD_RE.findall(claim_a.lower()))
    b_words = set(WORD_RE.findall(claim_b.lower()))

    if (
        _significant_word_count(a_words) > _MAX_STATUS_CLAIM_WORDS
        or _significant_word_count(b_words) > _MAX_STATUS_CLAIM_WORDS
    ):
        return False

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
