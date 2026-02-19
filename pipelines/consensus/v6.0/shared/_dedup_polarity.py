"""
Polarity detection for deduplication.

Detects when semantically similar statements make opposing claims,
preventing contradictions from being merged during deduplication.

Invariant: polarity_conflict(a, b) ==> not merge(a, b)
"""

from __future__ import annotations

import re
from typing import Protocol


class PolarityDetector(Protocol):
    """Strategy interface for polarity conflict detection."""

    def has_negation(self, text: str) -> bool:
        """Check if text contains negation markers."""
        ...

    def has_conflict(self, text_a: str, text_b: str) -> bool:
        """Check if two texts make opposing claims."""
        ...


class NegationPatternDetector:
    """
    Lightweight regex-based negation detection.

    Detects explicit negation patterns that indicate a statement
    is refuting or denying a claim rather than asserting it.
    """

    VERB_NEGATION = re.compile(
        r"\b(is|are|was|were|does|did|has|have|had|do|can|could|would|should)\s+not\b",
        re.IGNORECASE,
    )

    CONTRACTIONS = re.compile(
        r"\b(isn't|aren't|wasn't|weren't|doesn't|didn't|hasn't|haven't|hadn't|"
        r"don't|can't|couldn't|wouldn't|shouldn't|won't)\b",
        re.IGNORECASE,
    )

    NEGATION_PHRASES = re.compile(
        r"\b(no\s+(significant|particular|special|known|documented|evidence|proof)|"
        r"not\s+(a|an|the|any|particularly|especially|widely)|"
        r"contrary\s+to|"
        r"despite\s+(claims?|assertions?|beliefs?)|"
        r"incorrectly\s+(believed|stated|claimed)|"
        r"(false|incorrect|inaccurate|untrue|unfounded|baseless)|"
        r"(rarely|never|seldom|unlikely|improbable)|"
        r"lack\s+of|absence\s+of|without\s+)\b",
        re.IGNORECASE,
    )

    def has_negation(self, text: str) -> bool:
        """Check if text contains negation markers."""
        return bool(
            self.VERB_NEGATION.search(text)
            or self.CONTRACTIONS.search(text)
            or self.NEGATION_PHRASES.search(text)
        )

    def has_conflict(self, text_a: str, text_b: str) -> bool:
        """
        Detect polarity conflict between two semantically similar texts.

        Within a high-similarity cluster, if one statement has negation
        and the other doesn't, they're making opposing claims.
        """
        a_negated = self.has_negation(text_a)
        b_negated = self.has_negation(text_b)
        return a_negated != b_negated


def split_cluster_by_polarity(
    cluster: set[int],
    statements: list[str],
    detector: PolarityDetector | None = None,
) -> list[set[int]]:
    """
    Split cluster into polarity-coherent subclusters.

    Args:
        cluster: Set of statement indices in this cluster
        statements: All statement texts
        detector: Polarity detector (defaults to NegationPatternDetector)

    Returns:
        List of subclusters where no polarity conflicts exist within each.
        Returns [cluster] unchanged if no conflicts detected.
    """
    if len(cluster) <= 1:
        return [cluster]

    if detector is None:
        detector = NegationPatternDetector()

    negated = {i for i in cluster if detector.has_negation(statements[i])}
    affirmed = cluster - negated

    if affirmed and negated:
        return [affirmed, negated]

    return [cluster]
