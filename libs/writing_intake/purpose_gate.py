"""Deterministic purpose-relevance and register gates for writer-specialist intake."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

SWORN_CHANNELS = frozenset({"filing"})
PURPOSE_MIN_OVERLAP = 2
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an the and or of to in on at for by with from as is are was were be been this that these "
    "those it its i my me we our you your will would can could should may am do does did have has had not no any all "
    "so if then there here which who whom what when where how per via re".split()
)
_CORRESPONDENCE_GENERIC = frozenset(
    "email mail attachment attached attach message receipt confirm please thank "
    "thanks kindly regard reply let know further information require writing".split()
)
_DECLARATION_REGISTER = re.compile(
    r"^\s*I,\s+[^,\n]{2,80},\s+(?:am|do|hereby|state|declare|submit|certify)\b"
    r"|\bdeclare\s+under\s+penalty\s+of\s+perjury\b",
    re.I,
)


def check_class_for_channel(channel: str | None) -> Literal["sworn", "correspondence"]:
    """Return sworn for filing channel, else correspondence."""
    return "sworn" if channel in SWORN_CHANNELS else "correspondence"


def purpose_content_tokens(text: str) -> frozenset[str]:
    """Extract content tokens from text for purpose overlap scoring."""
    scrubbed = re.sub(r"\S+@\S+", " ", text.lower())
    tokens: set[str] = set()
    for match in _TOKEN.finditer(scrubbed):
        token = match.group(0)
        if len(token) < 2:
            continue
        if token in _STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        if token in _CORRESPONDENCE_GENERIC or token in _STOPWORDS:
            continue
        tokens.add(token)
    return frozenset(tokens)


def map_claim_to_purpose(claim: str, purpose_points: Sequence[str]) -> str | None:
    """Map claim to the best purpose point when overlap meets PURPOSE_MIN_OVERLAP.

    One-token overlap never maps a claim; correspondence logistics words
    (email, attachment, receipt, confirm, …) never count toward overlap.
    """
    if not purpose_points:
        return None
    claim_tokens = purpose_content_tokens(claim)
    best_index = 0
    best_score = 0
    for index, point in enumerate(purpose_points):
        score = len(claim_tokens & purpose_content_tokens(point))
        if score > best_score:
            best_score = score
            best_index = index
    if best_score >= PURPOSE_MIN_OVERLAP:
        return f"purpose[{best_index}]"
    return None


def is_declaration_register(sentence: str) -> bool:
    """True when sentence uses sworn declaration register phrasing."""
    return bool(_DECLARATION_REGISTER.search(sentence))
