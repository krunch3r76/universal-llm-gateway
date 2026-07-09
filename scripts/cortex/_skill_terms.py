"""Shared trigger_match_terms derivation helpers for ingest and backfill."""

from __future__ import annotations

import re

from cortex_store.routes._skill_suggest import STOPWORDS

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9_+.-]+")
_FOL_OPERATORS = {"∨", "∧", "⇒", "⇔", "¬", "→", "∈", "∉", "∪", "∩", "⊆", "⊂", "|"}
_MAX_TERMS = 12
_PROCEDURAL_STOPWORDS = frozenset(
    {"before", "when", "task", "read", "agent", "session", "any", "use"}
)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT_RE.split(text.lower()) if t]


def _domain_tokens(slug: str, skill_category: str) -> set[str]:
    out = set(_tokenize(slug))
    out.update(_tokenize(skill_category))
    if skill_category:
        out.add(skill_category.lower())
    return out


def _keep_term(term: str, *, domain_tokens: set[str]) -> bool:
    low = term.lower()
    if len(low) <= 2:
        return False
    if low in STOPWORDS or low in _PROCEDURAL_STOPWORDS:
        return low in domain_tokens
    return True


def canonicalize_trigger_match_terms(terms: list[str]) -> list[str]:
    """Dedupe (case-insensitive) and sort for stable projection and drift checks."""
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    out.sort(key=str.lower)
    return out


def derive_trigger_match_terms(
    slug: str,
    *,
    trigger_short: str = "",
    skill_category: str = "",
    description: str = "",
) -> list[str]:
    """Deterministic H3 derivation — cap 12, idempotent."""
    domain = _domain_tokens(slug, skill_category)
    terms: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        text = raw.strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        if not _keep_term(text, domain_tokens=domain):
            return
        seen.add(key)
        terms.append(text)

    add(slug)
    add(slug.replace("-", "_"))

    trigger_raw = trigger_short or ""
    for op in _FOL_OPERATORS:
        trigger_raw = trigger_raw.replace(op, " ")
    for tok in _tokenize(trigger_raw):
        add(tok)

    for tok in _tokenize(skill_category):
        add(tok)
    if skill_category and "-" in skill_category:
        add(skill_category)

    desc = (description or "")[:120]
    for tok in _tokenize(desc):
        add(tok)

    return terms[:_MAX_TERMS]


def derive_trigger_match_terms_from_vocab(
    slug: str,
    *,
    vocab_rows: list[tuple[str, str, str, float, int]],
    top_n: int = _MAX_TERMS,
) -> list[str]:
    """Top-N terms by score from skill_vocabulary rows for one slug."""
    slug_rows = [row for row in vocab_rows if row[0] == slug]
    slug_rows.sort(key=lambda row: (-row[3], row[2]))
    terms: list[str] = []
    seen: set[str] = set()
    for _slug, _register, term, _score, _chunks in slug_rows:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= top_n:
            break
    return terms


def derive_projection_trigger_match_terms(
    slug: str,
    *,
    frontmatter: dict[str, object],
    description: str,
    vocab_rows: list[tuple[str, str, str, float, int]] | None = None,
) -> list[str]:
    """Derive terms for ingest projection when frontmatter omits trigger_match_terms.

    Vocab rows take precedence when the caller pre-loads non-empty rows; otherwise
    description-only derivation applies (ingest_skills does not load vocab today).
    """
    if vocab_rows:
        vocab_terms = derive_trigger_match_terms_from_vocab(slug, vocab_rows=vocab_rows)
        if vocab_terms:
            return canonicalize_trigger_match_terms(vocab_terms)
    return canonicalize_trigger_match_terms(
        derive_trigger_match_terms(
            slug,
            trigger_short=str(frontmatter.get("trigger_short") or ""),
            skill_category=str(frontmatter.get("skill_category") or ""),
            description=description,
        )
    )
