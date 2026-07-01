"""Stage-A candidate fetch/score helpers (split from _skill_suggest.py)."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..confidence_field import (
    DISCOVERABLE_SKILL_LIFECYCLE,
    discoverable_skill_lifecycle_sql_predicate,
)
from ..db import query as db_query
from ..guidance_entity import strip_guidance_id_prefix
from ..seat_applicability import (
    CAPABILITY_CLAUSE,
    UNIVERSAL,
    canonical_seat_or_422,
    for_agent_filter_clause,
    seat_capabilities_json,
)
from ._skill_index import index_envelope_fields
from .boot._skill_trigger import skill_description_text

_DISCOVERABLE_SKILL_LIFECYCLE = discoverable_skill_lifecycle_sql_predicate()

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9_+.-]+")

_GENERIC_SINGLETON_MATCH_TERMS = frozenset(
    {"lead", "seat", "consult", "review", "agent", "skill", "decision"}
)

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "have",
        "he",
        "her",
        "his",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "need",
        "of",
        "on",
        "or",
        "our",
        "she",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
        "help",
        "want",
        "can",
        "do",
        "does",
        "did",
        "not",
        "no",
        "yes",
        "if",
        "but",
        "so",
        "than",
        "then",
        "there",
        "these",
        "those",
        "been",
        "being",
        "has",
        "had",
        "am",
        "about",
        "into",
        "over",
        "after",
        "before",
        "just",
        "also",
        "how",
        "why",
        "where",
        "all",
        "any",
        "some",
        "more",
        "most",
        "other",
        "such",
        "only",
        "own",
        "same",
        "too",
        "very",
        "should",
        "could",
        "may",
        "might",
        "must",
        "shall",
        "get",
        "got",
        "make",
        "made",
        "use",
        "using",
        "used",
    }
)

_RELATED_SKILLS_BOOST = 0.25

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:/{0,2}", re.IGNORECASE)
_DIR_LAYOUT_FILE_STEMS = frozenset({"skill", "readme"})

_SUGGEST_CANDIDATE_SQL = f"""
    SELECT id, name, description, source_uri,
           json_extract(attributes, '$.trigger_short') AS trigger_short,
           json_extract(attributes, '$.trigger_match_terms') AS trigger_match_terms_json,
           json_extract(attributes, '$.skill_category') AS skill_category,
           json_extract(attributes, '$.boot_importance') AS boot_importance,
           json_extract(attributes, '$.related_skills') AS related_skills_json,
           json_extract(attributes, '$.applicable_agents') AS applicable_agents_json,
           COALESCE(CAST(json_extract(attributes, '$.delivery_priority') AS INTEGER), 100)
               AS delivery_priority
    FROM entities
    WHERE type IN ('agent_skill', 'rule', 'skill')
      AND {_DISCOVERABLE_SKILL_LIFECYCLE}
      {{for_agent_filter}}{{capability_filter}}
"""


def norm_loaded(value: str) -> str:
    """Normalize a loaded slug/id for set membership (§3.2)."""
    s = strip_guidance_id_prefix(value).lower()
    if s.endswith(".md"):
        s = s[:-3]
    return s


def slug_from_source_uri(source_uri: str | None) -> str | None:
    """Canonical bare slug from source_uri stem (§1 — not entity name)."""
    if not source_uri:
        return None
    s = _SCHEME_RE.sub("", str(source_uri).strip()).strip("/")
    if not s:
        return None
    segments = [seg for seg in s.split("/") if seg]
    stem = segments[-1]
    if stem.endswith(".md"):
        stem = stem[:-3]
    if stem.lower() in _DIR_LAYOUT_FILE_STEMS and len(segments) >= 2:
        return segments[-2] or None
    return stem or None


def tokenize_text(text: str) -> set[str]:
    """Context tokenization per §3.3."""
    tokens = _TOKEN_SPLIT_RE.split(text.lower())
    return {t for t in tokens if len(t) >= 2 and t not in STOPWORDS}


def _decode_term_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    return [str(v).strip().lower() for v in values if str(v).strip()]


def _decode_related_slugs(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    slugs: list[str] = []
    for entry in values:
        slug = strip_guidance_id_prefix(str(entry).strip()).lower()
        slug = slug.split("#", 1)[0]
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def build_related_union(
    rows: list[dict[str, Any]], loaded_set: set[str]
) -> frozenset[str]:
    """Union of ``related_skills`` slugs declared on loaded parent rows."""
    by_slug: dict[str, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        slug = slug_from_source_uri(row.get("source_uri"))
        if slug:
            by_slug[norm_loaded(slug)] = row
        entity_id = str(row.get("id") or "")
        if entity_id:
            by_id[norm_loaded(entity_id)] = row

    union: set[str] = set()
    for loaded_key in loaded_set:
        parent = by_slug.get(loaded_key) or by_id.get(loaded_key)
        if not parent:
            continue
        union.update(_decode_related_slugs(parent.get("related_skills_json")))
    return frozenset(union)


def _decode_trigger_short(raw: str | None) -> list[str]:
    if not raw:
        return []
    return list(tokenize_text(str(raw)))


def _term_matches(term: str, ctx_tokens: set[str]) -> bool:
    parts = _TOKEN_SPLIT_RE.split(term.lower())
    parts = [p for p in parts if p]
    if not parts:
        return False
    if len(parts) == 1:
        return parts[0] in ctx_tokens
    return all(p in ctx_tokens for p in parts)


def matched_terms(
    trigger_terms: list[str], trigger_short_tokens: list[str], ctx_tokens: set[str]
) -> list[str]:
    seen: set[str] = set()
    matched: list[str] = []
    for term in trigger_terms:
        if term in seen:
            continue
        if _term_matches(term, ctx_tokens):
            seen.add(term)
            matched.append(term)
    for term in trigger_short_tokens:
        if term in seen:
            continue
        if term in ctx_tokens:
            seen.add(term)
            matched.append(term)
    return matched


def _priority_boost(delivery_priority: int) -> float:
    return (100 - min(delivery_priority, 100)) / 200


@dataclass(frozen=True)
class CandidateScore:
    """Scoring metadata for the deterministic precision gate."""

    score: float
    base_score: int
    matched_terms: tuple[str, ...]
    matched_specific_terms: frozenset[str]
    has_phrase_match: bool


def normalized_context(text: str) -> str:
    return " ".join(t for t in _TOKEN_SPLIT_RE.split(text.lower()) if t)


def has_contiguous_phrase_match(term: str, normalized_context: str) -> bool:
    normalized = " ".join(t for t in _TOKEN_SPLIT_RE.split(term.lower()) if t)
    if len(normalized.split()) < 2:
        return False
    return f" {normalized} " in f" {normalized_context} "


def is_specific_term(term: str) -> bool:
    tokens = [t for t in _TOKEN_SPLIT_RE.split(term.lower()) if t]
    if not tokens:
        return False
    if len(tokens) >= 2:
        return all(t not in _GENERIC_SINGLETON_MATCH_TERMS for t in tokens)
    return tokens[0] not in _GENERIC_SINGLETON_MATCH_TERMS


def passes_precision_gate(score: CandidateScore) -> bool:
    if score.has_phrase_match:
        return True
    return len(score.matched_specific_terms) >= 1


def _decode_applicable_agents(row: dict[str, Any]) -> list[str]:
    raw = row.get("applicable_agents_json")
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def row_applies_to_seat(row: dict[str, Any], canonical_seat: str) -> bool:
    agents = _decode_applicable_agents(row)
    if not agents:
        return False
    return UNIVERSAL in agents or canonical_seat in agents


def fetch_candidates(conn: sqlite3.Connection, agent: str) -> list[dict[str, Any]]:
    canonical = canonical_seat_or_422(agent)
    params: list[Any] = [
        DISCOVERABLE_SKILL_LIFECYCLE,
        canonical,
        seat_capabilities_json(canonical),
    ]
    sql = _SUGGEST_CANDIDATE_SQL.format(
        for_agent_filter=for_agent_filter_clause(canonical),
        capability_filter=CAPABILITY_CLAUSE,
    )
    return [dict(r) for r in db_query(conn, sql, tuple(params))]


def score_candidate(
    row: dict[str, Any],
    ctx_tokens: set[str],
    normalized_context: str,
    *,
    related_union: frozenset[str] = frozenset(),
) -> CandidateScore | None:
    slug = slug_from_source_uri(row.get("source_uri"))
    if not slug:
        return None
    trigger_terms = _decode_term_list(row.get("trigger_match_terms_json"))
    trigger_short_tokens = _decode_trigger_short(row.get("trigger_short"))
    matched = matched_terms(trigger_terms, trigger_short_tokens, ctx_tokens)
    base_score = len(matched)
    if base_score < 1:
        return None
    score = float(base_score)
    if row.get("boot_importance") == "required_gate":
        score += 0.5
    score += _priority_boost(int(row.get("delivery_priority") or 100))
    if norm_loaded(slug) in related_union:
        score += _RELATED_SKILLS_BOOST
    matched_specific = frozenset(m for m in matched if is_specific_term(m))
    has_phrase = any(
        has_contiguous_phrase_match(term, normalized_context) for term in trigger_terms
    )
    return CandidateScore(
        score=score,
        base_score=base_score,
        matched_terms=tuple(matched),
        matched_specific_terms=matched_specific,
        has_phrase_match=has_phrase,
    )


def sort_key(item: dict[str, Any]) -> tuple[float, int, int, str]:
    return (
        -item["score"],
        0 if item.get("boot_importance") == "required_gate" else 1,
        int(item.get("delivery_priority") or 100),
        item["slug"],
    )


def humanize_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip()


def suggestion_description(row: dict[str, Any]) -> str:
    description = skill_description_text(row)
    if description:
        return description
    slug = slug_from_source_uri(row.get("source_uri")) or str(row.get("name") or "")
    return humanize_slug(slug)


def build_extended_candidate(
    row: dict[str, Any], scored: CandidateScore | None
) -> dict[str, Any] | None:
    slug = slug_from_source_uri(row.get("source_uri"))
    if not slug:
        return None
    entity_id = str(row.get("id") or "")
    envelope = index_envelope_fields(row)
    score = float(scored.score) if scored is not None else 0.0
    return {
        "id": entity_id,
        "slug": slug,
        "source_uri": envelope.get("source_uri"),
        "digest": envelope.get("digest"),
        "score": score,
        "description": suggestion_description(row),
        "trigger_short": row.get("trigger_short") or "",
    }
