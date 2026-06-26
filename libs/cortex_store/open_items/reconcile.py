"""Pure open-items reconciliation — match open_items against resolved work.

Shared by cortex boot (mcp-server, operating on HTTP-fetched data) and the
control tower aggregation (cortex-api, server-side). No DB or framework
imports: matching is string/regex only so both call sites run identical logic
(``decision`` acceptance: "control tower and boot use the same reconciliation
function").

A *resolved record* is a dict describing recently-completed work:

    {"id": int | None,       # assertion id      → matches [assertion:N] / [ref:N]
     "slug": str | None,     # todo slug         → matches [todo:slug]
     "claim": str,           # claim/description → fuzzy token + phrase match
     "entity_name": str}     # entity name       → fuzzy token + phrase match

An *open_item* is reconciled when it carries a ref tag pointing at a resolved
record, or — for legacy untagged items — when its discriminating tokens or
opening phrase overlap a resolved claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Deterministic ref tags appended to open_items by agents.
_ASSERTION_REF_RE = re.compile(r"\[(?:assertion|ref):(\d+)\]", re.IGNORECASE)
_TODO_REF_RE = re.compile(r"\[todo:([a-z0-9][a-z0-9-]*)\]", re.IGNORECASE)
_BARE_TODO_PREFIX_RE = re.compile(r"^\s*todo:([a-z0-9][a-z0-9-]*)", re.IGNORECASE)

_RESOLVED_PREFIX = "[RESOLVED]"

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
        "the",
        "a",
        "an",
        "and",
        "or",
        "not",
        "on",
        "in",
        "at",
        "to",
        "of",
        "for",
        "from",
        "by",
        "as",
        "with",
        "its",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "be",
        "been",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "he",
        "she",
        "it",
        "they",
        "we",
        "there",
        "this",
        "that",
        "no",
        "yes",
        "due",
        "paid",
        "payment",
        "minimum",
        "balance",
        "confirmed",
        "recorded",
        "yet",
        "still",
        "upcoming",
    }
)

_GENERIC_STARTERS = frozenset(
    {
        "has",
        "have",
        "had",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "he",
        "she",
        "it",
        "they",
        "we",
        "kaywan",
        "there",
        "this",
        "that",
    }
)

_COMMON_SHORT = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "not",
        "on",
        "in",
        "at",
        "to",
        "of",
        "for",
        "from",
        "by",
        "as",
        "with",
        "its",
    }
)


def _extract_discriminating_tokens(text: str) -> set[str]:
    """Pull dollar amounts, dates, account suffixes, and distinctive words.

    Returns lowercased tokens that distinguish one matter from another. Used
    for fuzzy matching when ref tags are unavailable.
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


def _resolved_key_phrases(resolved: list[dict[str, Any]]) -> set[str]:
    """First-4-word phrases of resolved claims, keyed for substring match.

    Only phrases with a distinguishing token (digit/dollar, or an 8+ char
    word) are kept, and phrases opening with a generic starter are excluded,
    to avoid false positives against unrelated open_items.
    """
    phrases: set[str] = set()
    for r in resolved:
        claim = r.get("claim") or ""
        words = claim.split()[:4]
        if len(words) < 3:
            continue
        if words[0].lower().rstrip(".,;:") in _GENERIC_STARTERS:
            continue
        has_distinguishing = any(
            any(ch.isdigit() or ch == "$" for ch in w)
            or (len(w) >= 8 and w.lower().rstrip(".,;:") not in _COMMON_SHORT)
            for w in words
        )
        if has_distinguishing:
            phrases.add(" ".join(words).lower())
    return phrases


@dataclass
class ResolutionIndex:
    """Pre-computed matchers for a set of resolved records."""

    resolved_ids: set[int] = field(default_factory=set)
    resolved_slugs: set[str] = field(default_factory=set)
    token_sets: list[set[str]] = field(default_factory=list)
    key_phrases: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (
            self.resolved_ids
            or self.resolved_slugs
            or self.token_sets
            or self.key_phrases
        )


def build_resolution_index(resolved: list[dict[str, Any]]) -> ResolutionIndex:
    """Build the dual deterministic/fuzzy index from resolved records."""
    index = ResolutionIndex()
    for r in resolved:
        aid = r.get("id")
        if aid is not None:
            index.resolved_ids.add(int(aid))
        slug = r.get("slug")
        if slug:
            index.resolved_slugs.add(str(slug).lower().replace("todo:", "", 1))
        combined = (r.get("claim") or "") + " " + (r.get("entity_name") or "")
        tokens = _extract_discriminating_tokens(combined)
        if len(tokens) >= 2:
            index.token_sets.append(tokens)
    index.key_phrases = _resolved_key_phrases(resolved)
    return index


def is_resolved(item: str, index: ResolutionIndex) -> bool:
    """True if an open_item matches a resolved record via any strategy.

    1. Assertion ref  — [assertion:ID] / [ref:ID] matching a resolved id
    2. Todo ref       — [todo:slug] matching a resolved todo slug
    3. Phrase match   — first 4 words of a resolved claim appear (legacy)
    4. Token overlap  — 2+ discriminating tokens shared with a resolved claim
    """
    for m in _ASSERTION_REF_RE.finditer(item):
        if int(m.group(1)) in index.resolved_ids:
            return True
    for m in _TODO_REF_RE.finditer(item):
        if m.group(1).lower() in index.resolved_slugs:
            return True
    bare = _BARE_TODO_PREFIX_RE.match(item)
    if bare and bare.group(1).lower() in index.resolved_slugs:
        return True

    item_lower = item.lower()
    if any(phrase in item_lower for phrase in index.key_phrases):
        return True

    if index.token_sets:
        item_tokens = _extract_discriminating_tokens(item)
        if item_tokens:
            for resolved_tokens in index.token_sets:
                if len(item_tokens & resolved_tokens) >= 2:
                    return True
    return False


def reconcile_open_items(
    open_items: list[Any],
    resolved: list[dict[str, Any]] | None = None,
    *,
    index: ResolutionIndex | None = None,
    omit_resolved: bool = False,
) -> list[str]:
    """Reconcile open_items against resolved work.

    Pass either ``resolved`` records (a fresh index is built) or a prebuilt
    ``index`` (cheaper when reconciling many sessions against one resolved set).

    When ``omit_resolved`` is False, matched items get a ``[RESOLVED]`` prefix
    (boot keeps them for audit). When True, matched items are dropped entirely
    (control tower display). Already-prefixed items are preserved on tag mode
    and dropped on omit mode.

    Note: Entities of ``closure_audit_exempt`` types (e.g. ``condition``) must
    never be passed as open_items. Callers that generate the open_items list
    from a database query MUST filter closure_audit_exempt entity types
    upstream using ``filter_closure_exempt_items`` below.
    """
    items = [str(i) for i in (open_items or [])]
    if not items:
        return items
    idx = index if index is not None else build_resolution_index(resolved or [])

    out: list[str] = []
    for item in items:
        already = item.startswith(_RESOLVED_PREFIX)
        # Pre-tagged items are resolved regardless of the current index; fresh
        # matching only runs when the index has something to match against.
        resolved_hit = already or (not idx.is_empty() and is_resolved(item, idx))
        if resolved_hit:
            if omit_resolved:
                continue
            out.append(item if already else f"{_RESOLVED_PREFIX} {item}")
        else:
            out.append(item)
    return out
