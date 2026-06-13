"""Frozen Stage-A deterministic skill suggestion engine (todo:skill-suggest-mcp-tool §3)."""

from __future__ import annotations

import json
import re
from typing import Any

from ..confidence_field import lifecycle_not_value_sql_predicate
from ..db import cortex_conn
from ..db import query as db_query
from ..seat_applicability import (
    CAPABILITY_CLAUSE,
    FOR_AGENT_CLAUSE,
    canonical_seat_or_422,
    seat_capabilities_json,
)

_DEPRECATED_EXCLUDE = lifecycle_not_value_sql_predicate("deprecated")

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9_+.-]+")

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

_SUGGEST_CANDIDATE_SQL = f"""
    SELECT id, name, source_uri,
           json_extract(attributes, '$.trigger_short') AS trigger_short,
           json_extract(attributes, '$.trigger_match_terms') AS trigger_match_terms_json,
           json_extract(attributes, '$.skill_category') AS skill_category,
           json_extract(attributes, '$.boot_importance') AS boot_importance,
           COALESCE(CAST(json_extract(attributes, '$.delivery_priority') AS INTEGER), 100)
               AS delivery_priority
    FROM entities
    WHERE type = 'agent_skill'
      AND {_DEPRECATED_EXCLUDE}
      {{for_agent_filter}}{{capability_filter}}
"""


def norm_loaded(value: str) -> str:
    """Normalize a loaded slug/id for set membership (§3.2)."""
    s = value.strip().lower()
    if s.startswith("agent_skill:"):
        s = s.removeprefix("agent_skill:")
    if s.endswith(".md"):
        s = s[:-3]
    return s


def build_loaded_set(loaded: list[str]) -> set[str]:
    return {norm_loaded(x) for x in loaded if isinstance(x, str) and x.strip()}


def slug_from_source_uri(source_uri: str | None) -> str | None:
    """Canonical slug from source_uri stem (§1 — not entity name)."""
    if not source_uri:
        return None
    s = str(source_uri).strip()
    if s.startswith("agent-skills/"):
        s = s[len("agent-skills/") :]
    if s.endswith(".md"):
        s = s[:-3]
    return s or None


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


def _decode_trigger_short(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t for t in tokenize_text(str(raw))]


def _term_matches(term: str, ctx_tokens: set[str]) -> bool:
    parts = _TOKEN_SPLIT_RE.split(term.lower())
    parts = [p for p in parts if p]
    if not parts:
        return False
    if len(parts) == 1:
        return parts[0] in ctx_tokens
    return all(p in ctx_tokens for p in parts)


def _matched_terms(
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


def _is_loaded(candidate_slug: str, entity_id: str, loaded_set: set[str]) -> bool:
    slug_norm = norm_loaded(candidate_slug)
    id_norm = norm_loaded(entity_id)
    return (
        slug_norm in loaded_set
        or id_norm in loaded_set
        or norm_loaded(f"agent_skill:{candidate_slug}") in loaded_set
    )


def _priority_boost(delivery_priority: int) -> float:
    return (100 - min(delivery_priority, 100)) / 200


def _fetch_candidates(agent: str) -> list[dict[str, Any]]:
    canonical = canonical_seat_or_422(agent)
    params: list[Any] = ["deprecated", canonical, seat_capabilities_json(canonical)]
    sql = _SUGGEST_CANDIDATE_SQL.format(
        for_agent_filter=FOR_AGENT_CLAUSE,
        capability_filter=CAPABILITY_CLAUSE,
    )
    conn = cortex_conn()
    try:
        return [dict(r) for r in db_query(conn, sql, tuple(params))]
    finally:
        conn.close()


def _score_candidate(
    row: dict[str, Any], ctx_tokens: set[str]
) -> tuple[float, list[str]] | None:
    slug = slug_from_source_uri(row.get("source_uri"))
    if not slug:
        return None
    trigger_terms = _decode_term_list(row.get("trigger_match_terms_json"))
    trigger_short_tokens = _decode_trigger_short(row.get("trigger_short"))
    matched = _matched_terms(trigger_terms, trigger_short_tokens, ctx_tokens)
    base_score = len(matched)
    if base_score < 1:
        return None
    score = float(base_score)
    if row.get("boot_importance") == "required_gate":
        score += 0.5
    score += _priority_boost(int(row.get("delivery_priority") or 100))
    return score, matched


def _sort_key(
    item: dict[str, Any],
) -> tuple[float, int, int, str]:
    return (
        -item["score"],
        0 if item.get("boot_importance") == "required_gate" else 1,
        int(item.get("delivery_priority") or 100),
        item["slug"],
    )


def run_stage_a(
    *,
    agent: str,
    loaded: list[str],
    conversation_context: str | None,
    limit: int,
) -> dict[str, Any]:
    """Pure deterministic Stage-A engine (§3)."""
    ctx = (conversation_context or "").strip()
    if not ctx:
        return {
            "suggestions": [],
            "loaded_echo": [],
            "omitted": [],
            "ranker_status": "skipped_no_context",
            "degraded": False,
            "agent": canonical_seat_or_422(agent),
            "count": 0,
            "reason": "insufficient_context",
            "stage_a_candidates": [],
        }

    loaded_set = build_loaded_set(loaded)
    ctx_tokens = tokenize_text(ctx)
    canonical_agent = canonical_seat_or_422(agent)
    rows = _fetch_candidates(agent)

    scored_loaded: list[dict[str, Any]] = []
    scored_new: list[dict[str, Any]] = []
    loaded_echo: list[str] = []

    for row in rows:
        slug = slug_from_source_uri(row.get("source_uri"))
        if not slug:
            continue
        entity_id = str(row.get("id") or "")
        scored = _score_candidate(row, ctx_tokens)
        if scored is None:
            continue
        score, matched = scored
        trigger_terms = _decode_term_list(row.get("trigger_match_terms_json"))
        entry = {
            "id": entity_id,
            "slug": slug,
            "uri": f"cortex://agent-skills/{slug}.md",
            "score": score,
            "trigger_match": matched[:5],
            "reason": "matches: " + ", ".join(matched[:5]),
            "reason_source": "deterministic",
            "boot_importance": row.get("boot_importance"),
            "delivery_priority": int(row.get("delivery_priority") or 100),
            "trigger_short": row.get("trigger_short") or "",
            "trigger_match_terms": trigger_terms,
            "skill_category": row.get("skill_category") or "",
        }
        if _is_loaded(slug, entity_id, loaded_set):
            if slug not in loaded_echo:
                loaded_echo.append(slug)
            scored_loaded.append(entry)
        else:
            scored_new.append(entry)

    loaded_echo = sorted(set(loaded_echo))
    scored_new.sort(key=_sort_key)
    suggestions = [
        {k: v for k, v in item.items() if k not in {"boot_importance", "delivery_priority"}}
        for item in scored_new[:limit]
    ]
    omitted = [
        {"slug": item["slug"], "reason": "already_loaded"}
        for item in sorted(scored_loaded, key=_sort_key)
    ]

    return {
        "suggestions": suggestions,
        "loaded_echo": loaded_echo,
        "omitted": omitted,
        "ranker_status": "disabled",
        "degraded": False,
        "agent": canonical_agent,
        "count": len(suggestions),
        "stage_a_candidates": scored_new,
    }
