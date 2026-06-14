"""Frozen Stage-A deterministic skill suggestion engine (todo:skill-suggest-mcp-tool §3)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
from ._skill_index import index_envelope_fields

_DEPRECATED_EXCLUDE = lifecycle_not_value_sql_predicate("deprecated")

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9_+.-]+")

# Generic singleton match terms that bleed into the tail on unrelated contexts
# (thread 1881 reviewer verdict — deterministic precision gate). A candidate
# whose ONLY matched evidence is one of these is dropped unless it also has a
# contiguous multi-token phrase match. Routing-meaningful tokens (handoff,
# dispatch, packet, consensus, steelman, …) are deliberately NOT generic.
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


_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:/{0,2}", re.IGNORECASE)

# Directory-layout skills store their body at `…/<slug>/SKILL.md` (or README.md);
# the filename stem is a fixed convention marker, NOT the slug (friction 17551).
_DIR_LAYOUT_FILE_STEMS = frozenset({"skill", "readme"})


def slug_from_source_uri(source_uri: str | None) -> str | None:
    """Canonical bare slug from source_uri stem (§1 — not entity name).

    Robust to scheme- or path-carrying source_uris (`cortex://…`,
    `workspaces://…/skills/…`, malformed `cortex:agent-skills/…`): strip any
    scheme, then derive the slug from the path tail. Two layouts:

    * Flat-file skill — `…/<slug>.md` → slug is the filename stem (thread 1876).
    * Directory-layout skill — `…/<slug>/SKILL.md` (or `README.md`) → the stem is
      a convention marker; the slug is the **parent directory** (friction 17551).
      Without this every directory-layout skill collapses to slug "SKILL" and the
      assembled uris collide on a single `cortex://agent-skills/SKILL.md`.

    The slug MUST stay a bare token — `run_stage_a` uses it for load-state
    dedup (`_is_loaded`) and as the entry `slug` identity field. The suggestion
    uri is NO LONGER slug-derived: SF1 (todo:skill-suggest-authoritative-uri)
    routes provenance through `index_envelope_fields(row)` →
    `{source_uri, digest}`, dropping the old `cortex://agent-skills/{slug}.md`
    assembly that 404'd on directory-layout skills (thread 1876 gap table).
    """
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


@dataclass(frozen=True)
class _CandidateScore:
    """Scoring metadata for the deterministic precision gate (thread 1881).

    `score` is the boosted total used for sorting; the gate reads ONLY the
    base deterministic signals (`matched_specific_terms`, `has_phrase_match`)
    so required-gate / delivery-priority boosts cannot rescue generic tail
    bleed.
    """

    score: float
    base_score: int
    matched_terms: tuple[str, ...]
    matched_specific_terms: frozenset[str]
    has_phrase_match: bool


def _normalized_context(text: str) -> str:
    """Whitespace-joined token stream of `text` — phrase-match substrate.

    Reuses the canonical token split (no stopword removal here: a phrase like
    'lead seat boot' must survive intact for contiguous matching)."""
    return " ".join(t for t in _TOKEN_SPLIT_RE.split(text.lower()) if t)


def _has_contiguous_phrase_match(term: str, normalized_context: str) -> bool:
    """True iff `term` is multi-token and appears contiguously in context."""
    normalized = " ".join(t for t in _TOKEN_SPLIT_RE.split(term.lower()) if t)
    if len(normalized.split()) < 2:
        return False
    return f" {normalized} " in f" {normalized_context} "


def _is_specific_term(term: str) -> bool:
    """A matched term is specific iff it carries only non-generic tokens.

    `_matched_terms` counts a multi-token trigger term whenever ALL its tokens
    appear anywhere in context (subset, NOT contiguous). Multi-token terms
    require EVERY token to be non-generic — otherwise a phrase like
    'decision point' would masquerade as specific on the incidental token
    'point' while 'decision' is generic prose. Such mixed phrases must clear
    the gate only via contiguous phrase rescue (`_has_contiguous_phrase_match`).
    Single-token terms pass iff that token is not in the generic singleton set."""
    tokens = [t for t in _TOKEN_SPLIT_RE.split(term.lower()) if t]
    if not tokens:
        return False
    if len(tokens) >= 2:
        return all(t not in _GENERIC_SINGLETON_MATCH_TERMS for t in tokens)
    return tokens[0] not in _GENERIC_SINGLETON_MATCH_TERMS


def _passes_precision_gate(score: _CandidateScore) -> bool:
    """Drop tail candidates whose only evidence is generic singleton token(s).

    Pass iff a contiguous multi-token phrase matched, or at least one specific
    (non-generic) term matched. Reads base metadata only — required-gate and
    delivery-priority boosts cannot rescue generic-only bleed.

    Deviation from the thread-1881 reviewer's literal ``>= 2 specific`` rule:
    the reviewer's target was generic-singleton bleed (lead/seat/consult), but
    ``>= 2`` also drops legitimate single-SPECIFIC-term matches that the system
    contractually surfaces (`test_prompt_injection_treated_as_tokens` — a lone
    specific token must still recommend its skill). ``>= 1 specific`` kills the
    named bleeders identically while preserving that contract.
    """
    if score.has_phrase_match:
        return True
    return len(score.matched_specific_terms) >= 1


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
    row: dict[str, Any], ctx_tokens: set[str], normalized_context: str
) -> _CandidateScore | None:
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
    matched_specific = frozenset(m for m in matched if _is_specific_term(m))
    has_phrase = any(
        _has_contiguous_phrase_match(term, normalized_context) for term in trigger_terms
    )
    return _CandidateScore(
        score=score,
        base_score=base_score,
        matched_terms=tuple(matched),
        matched_specific_terms=matched_specific,
        has_phrase_match=has_phrase,
    )


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
    """Pure deterministic Stage-A engine (§3).

    degraded_skills scope: captures only entities where slug_from_source_uri()
    returned None — null, empty, or scheme-only source_uri (e.g. bare "cortex://",
    "workspaces://"). Skills with a structurally valid source_uri pointing at a
    missing or unreadable file are NOT captured here: they receive a non-None slug,
    pass through scoring normally, and surface in suggestions with digest=None.
    Consumers needing a complete view of unreachable skills must check both signals:
      degraded_skills  — source_uri-derivation failures (slug underivable)
      null digests in suggestions — body-unresolvable (URI valid, file missing/404)
    This two-signal design is intentional and test-backed: offline tests cannot
    resolve files, so body-unresolvable skills are contractually expected in
    suggestions with digest=None rather than in degraded_skills.
    """
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
            "degraded_skills": [],
        }

    loaded_set = build_loaded_set(loaded)
    ctx_tokens = tokenize_text(ctx)
    normalized_context = _normalized_context(ctx)
    canonical_agent = canonical_seat_or_422(agent)
    rows = _fetch_candidates(agent)

    scored_loaded: list[dict[str, Any]] = []
    scored_new: list[dict[str, Any]] = []
    loaded_echo: list[str] = []
    degraded_skills: list[dict[str, Any]] = []

    for row in rows:
        source_uri_val = row.get("source_uri")
        slug = slug_from_source_uri(source_uri_val)
        if not slug:
            degraded_skills.append({
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "source_uri": source_uri_val,
                "skill_category": row.get("skill_category") or "",
                "degraded": True,
                "reason": "source_uri_null" if not source_uri_val else "source_uri_unparseable",
            })
            continue
        entity_id = str(row.get("id") or "")
        scored = _score_candidate(row, ctx_tokens, normalized_context)
        if scored is None:
            continue
        matched = list(scored.matched_terms)
        trigger_terms = _decode_term_list(row.get("trigger_match_terms_json"))
        envelope = index_envelope_fields(row)
        entry = {
            "id": entity_id,
            "slug": slug,
            **envelope,
            "score": scored.score,
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
        elif _passes_precision_gate(scored):
            scored_new.append(entry)

    loaded_echo = sorted(set(loaded_echo))
    scored_new.sort(key=_sort_key)
    suggestions = [
        {
            k: v
            for k, v in item.items()
            if k not in {"boot_importance", "delivery_priority"}
        }
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
        "degraded": bool(degraded_skills),
        "agent": canonical_agent,
        "count": len(suggestions),
        "stage_a_candidates": scored_new,
        "degraded_skills": degraded_skills,
    }
