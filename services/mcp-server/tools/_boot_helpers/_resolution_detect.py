"""Resolved-assertion detection: regex patterns, token extraction, filter function."""

from __future__ import annotations

import re
from typing import Any

# ── Assertion-ref pattern ────────────────────────────────────────────────────
# Matches [assertion:841] or [ref:841] tags appended to open_items by agents.
_ASSERTION_REF_RE = re.compile(r"\[(?:assertion|ref):(\d+)\]", re.IGNORECASE)

# ── Token extraction for fuzzy fallback ──────────────────────────────────────
_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
_DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"  # ISO dates
    r"|(?:january|february|march|april|may|june|july|august"
    r"|september|october|november|december)\s+\d{1,2}"  # "April 12"
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?",  # MM/DD or MM/DD/YYYY
    re.IGNORECASE,
)
_ACCOUNT_SUFFIX_RE = re.compile(r"(?:···|\.{3}|\*{3,4})(\d{4})")

_STOP_WORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "not", "on", "in", "at", "to", "of",
        "for", "from", "by", "as", "with", "its", "is", "are", "was", "were",
        "has", "have", "had", "be", "been", "will", "would", "could", "should",
        "may", "might", "he", "she", "it", "they", "we", "there", "this",
        "that", "no", "yes", "due", "paid", "payment", "minimum", "balance",
        "confirmed", "recorded", "yet", "still", "upcoming",
    }
)


def _extract_discriminating_tokens(text: str) -> set[str]:
    """Pull dollar amounts, dates, account suffixes, and distinctive words.

    Returns a set of lowercased tokens that distinguish one financial
    obligation from another. Used for fuzzy matching when assertion refs
    are unavailable.
    """
    tokens: set[str] = set()

    for m in _DOLLAR_RE.finditer(text):
        tokens.add(m.group().replace(",", "").lower())

    for m in _DATE_RE.finditer(text):
        tokens.add(m.group().lower())

    for m in _ACCOUNT_SUFFIX_RE.finditer(text):
        tokens.add(m.group(1))

    for word in re.findall(r"[a-zA-Z]{4,}", text):
        w = word.lower()
        if w not in _STOP_WORDS:
            tokens.add(w)

    return tokens


def _resolved_key_phrases(recently_resolved: list[dict[str, Any]]) -> set[str]:
    """Extract specific key phrases from recently-resolved temporal claims.

    Uses the first 4 words of each claim as a matching key. Only phrases that
    contain a distinguishing token — a digit/dollar amount, or a word with 8+
    characters — are included. Phrases starting with generic sentence openers
    (pronouns, helper verbs) are excluded to avoid false-positive matches
    against unrelated open_items describing similar-but-distinct events.
    """
    generic_starters = frozenset(
        {
            "has", "have", "had", "is", "are", "was", "were", "be", "been",
            "will", "would", "could", "should", "may", "might", "he", "she",
            "it", "they", "we", "kaywan", "there", "this", "that",
        }
    )
    common_short = frozenset(
        {
            "the", "a", "an", "and", "or", "not", "on", "in", "at", "to",
            "of", "for", "from", "by", "as", "with", "its",
        }
    )

    phrases: set[str] = set()
    for r in recently_resolved:
        claim = r.get("claim") or ""
        words = claim.split()[:4]
        if len(words) < 3:
            continue
        if words[0].lower().rstrip(".,;:") in generic_starters:
            continue
        phrase = " ".join(words).lower()
        has_distinguishing = any(
            any(ch.isdigit() or ch == "$" for ch in w)
            or (len(w) >= 8 and w.lower().rstrip(".,;:") not in common_short)
            for w in words
        )
        if has_distinguishing:
            phrases.add(phrase)
    return phrases


def _build_resolved_index(
    recently_resolved: list[dict[str, Any]],
) -> tuple[set[int], list[set[str]]]:
    """Build dual index for resolution matching.

    Returns:
        resolved_ids: assertion IDs that were resolved (for ref matching)
        resolved_token_sets: list of discriminating token sets, one per
            resolved assertion (for fuzzy fallback — match if 2+ tokens
            overlap with an open_item)
    """
    resolved_ids: set[int] = set()
    resolved_token_sets: list[set[str]] = []

    for r in recently_resolved:
        aid = r.get("id")
        if aid is not None:
            resolved_ids.add(int(aid))

        combined = (r.get("claim") or "") + " " + (r.get("entity_name") or "")
        tokens = _extract_discriminating_tokens(combined)
        if len(tokens) >= 2:
            resolved_token_sets.append(tokens)

    return resolved_ids, resolved_token_sets


def _is_resolved(
    item: str,
    resolved_ids: set[int],
    resolved_token_sets: list[set[str]],
    key_phrases: set[str],
) -> bool:
    """Check if an open_item matches a resolved assertion via three strategies.

    Strategy 1 (deterministic): assertion ref tag [assertion:ID]
    Strategy 2 (legacy): first-4-word phrase substring match
    Strategy 3 (fuzzy fallback): 2+ discriminating token overlap
    """
    item_lower = item.lower()

    for m in _ASSERTION_REF_RE.finditer(item):
        if int(m.group(1)) in resolved_ids:
            return True

    if any(phrase in item_lower for phrase in key_phrases):
        return True

    if resolved_token_sets:
        item_tokens = _extract_discriminating_tokens(item)
        if item_tokens:
            for resolved_tokens in resolved_token_sets:
                overlap = item_tokens & resolved_tokens
                if len(overlap) >= 2:
                    return True

    return False


def filter_stale_open_items(
    sessions: list[dict[str, Any]],
    recently_resolved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tag open_items in sessions that reference recently-resolved temporal matters.

    Resolution is detected via three strategies (checked in order):
    1. Assertion ref: open_item contains [assertion:ID] matching a resolved ID
    2. Phrase match: first 4 words of resolved claim appear in open_item (legacy)
    3. Token overlap: 2+ discriminating tokens (dollar amounts, dates, account
       suffixes, distinctive words) shared between resolved claim and open_item

    Matched items receive a '[RESOLVED]' prefix. This prevents the item from
    appearing as actionable in future boot briefings while preserving an audit
    trail.
    """
    if not recently_resolved:
        return sessions

    resolved_ids, resolved_token_sets = _build_resolved_index(recently_resolved)
    key_phrases = _resolved_key_phrases(recently_resolved)

    if not resolved_ids and not key_phrases and not resolved_token_sets:
        return sessions

    result: list[dict[str, Any]] = []
    for session in sessions:
        open_items = session.get("open_items") or []
        tagged: list[str] = []
        for item in open_items:
            if _is_resolved(str(item), resolved_ids, resolved_token_sets, key_phrases):
                tagged.append(f"[RESOLVED] {item}")
            else:
                tagged.append(item)
        result.append({**session, "open_items": tagged})
    return result
