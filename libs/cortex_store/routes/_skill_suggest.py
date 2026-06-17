"""Frozen Stage-A deterministic skill suggestion engine (todo:skill-suggest-mcp-tool §3)."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_seat.body_injection import (
    CODING_SESSION_BUNDLE,
    is_web_seat_slug,
    web_auto_inject_skill_slugs,
)

from ..confidence_field import (
    DISCOVERABLE_SKILL_LIFECYCLE,
    discoverable_skill_lifecycle_sql_predicate,
)
from ..db import cortex_conn
from ..db import query as db_query
from ..seat_applicability import (
    CAPABILITY_CLAUSE,
    canonical_seat_or_422,
    for_agent_filter_clause,
    seat_capabilities_json,
)
from ._skill_index import index_envelope_fields
from .boot._skill_trigger import skill_description_text

_DISCOVERABLE_SKILL_LIFECYCLE = discoverable_skill_lifecycle_sql_predicate()

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

# Deterministic 1-hop companion boost when a loaded parent declares the slug in
# related_skills (thread 2011 F4 — no transitive closure, no relationship walk).
_RELATED_SKILLS_BOOST = 0.25

_CODING_SESSION_START_TRIGGER = "coding-session-start"
_CODING_SESSION_CONTEXT_PHRASES = (
    "coding session",
    "code-touching",
    "implement in repo",
    "diff review",
    "diff-review",
    "code modification",
    "session review",
)

_GIT_POSTURE_MD_LIST_PATH = (
    "universal-llm-gateway/docs/agent-guides/skills/git-posture.md"
)
_GIT_POSTURE_CODING_SESSION_NUDGE = (
    f"git-posture: fs(workspaces, md_list {_GIT_POSTURE_MD_LIST_PATH}) → "
    "md_read § Execution lanes | Commit posture | What not to infer"
)


def _coding_session_bundle_slugs() -> frozenset[str]:
    inject = tuple(
        entity_id.removeprefix("agent_skill:")
        for entity_id in CODING_SESSION_BUNDLE["inject"]
    )
    return frozenset((*inject, *CODING_SESSION_BUNDLE["advertise"]))


def _matches_coding_session_start(ctx: str) -> bool:
    lowered = ctx.lower()
    return any(phrase in lowered for phrase in _CODING_SESSION_CONTEXT_PHRASES)


def _explicit_session_close_intent(ctx: str) -> bool:
    lowered = ctx.lower()
    return "session close" in lowered or "session-close" in lowered


def _suppress_session_close_false_positive(ctx: str, slug: str) -> bool:
    return (
        slug == "session-close"
        and _matches_coding_session_start(ctx)
        and not _explicit_session_close_intent(ctx)
    )


def _apply_coding_session_start(
    scored_new: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    loaded_set: set[str],
    ctx: str,
) -> list[dict[str, Any]]:
    if not _matches_coding_session_start(ctx):
        return scored_new

    filtered = [
        item
        for item in scored_new
        if not _suppress_session_close_false_positive(ctx, item["slug"])
    ]
    present = {item["slug"] for item in filtered}
    bundle_slugs = _coding_session_bundle_slugs()
    by_slug: dict[str, dict[str, Any]] = {}
    for row in rows:
        slug = slug_from_source_uri(row.get("source_uri"))
        if slug:
            by_slug[slug] = row

    for slug in sorted(bundle_slugs):
        if slug in present:
            continue
        row = by_slug.get(slug)
        if row is None:
            continue
        entity_id = str(row.get("id") or "")
        if _is_loaded(slug, entity_id, loaded_set):
            continue
        trigger_terms = _decode_term_list(row.get("trigger_match_terms_json"))
        envelope = index_envelope_fields(row)
        description = _suggestion_description(row)
        if description:
            reason = f"Recommended for coding session: {description}"
        else:
            reason = "Recommended for coding session start"
        if slug == "git-posture":
            reason = f"{reason}; {_GIT_POSTURE_CODING_SESSION_NUDGE}"
        filtered.append(
            {
                "id": entity_id,
                "slug": slug,
                **envelope,
                "score": 100.0,
                "description": description,
                "trigger_match": [_CODING_SESSION_START_TRIGGER],
                "reason": reason,
                "reason_source": "deterministic",
                "boot_importance": row.get("boot_importance"),
                "delivery_priority": int(row.get("delivery_priority") or 100),
                "trigger_short": row.get("trigger_short") or "",
                "trigger_match_terms": trigger_terms,
                "skill_category": row.get("skill_category") or "",
            }
        )
        present.add(slug)
    return filtered


_SUGGEST_CANDIDATE_SQL = f"""
    SELECT id, name, description, source_uri,
           json_extract(attributes, '$.trigger_short') AS trigger_short,
           json_extract(attributes, '$.trigger_match_terms') AS trigger_match_terms_json,
           json_extract(attributes, '$.skill_category') AS skill_category,
           json_extract(attributes, '$.boot_importance') AS boot_importance,
           json_extract(attributes, '$.related_skills') AS related_skills_json,
           COALESCE(CAST(json_extract(attributes, '$.delivery_priority') AS INTEGER), 100)
               AS delivery_priority
    FROM entities
    WHERE type = 'agent_skill'
      AND {_DISCOVERABLE_SKILL_LIFECYCLE}
      {{for_agent_filter}}{{capability_filter}}
"""

_PUBLIC_SUGGESTION_KEYS = frozenset(
    {
        "id",
        "slug",
        "source_uri",
        "digest",
        "score",
        "description",
        "reason",
        "reason_source",
    }
)


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


def _seat_preloaded_norm_slugs(agent: str) -> frozenset[str]:
    """Web auto-inject slugs merged into loaded_set (Slice F tracking)."""
    canonical = canonical_seat_or_422(agent)
    if not is_web_seat_slug(canonical):
        return frozenset()
    return frozenset(norm_loaded(slug) for slug in web_auto_inject_skill_slugs())


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
        slug = str(entry).strip().lower()
        if slug.startswith("agent_skill:"):
            slug = slug.removeprefix("agent_skill:")
        slug = slug.split("#", 1)[0]
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def _build_related_union(
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


def _public_suggestion(entry: dict[str, Any]) -> dict[str, Any]:
    """Agent-facing suggestion shape — descriptions, not tag evidence."""
    return {k: entry[k] for k in _PUBLIC_SUGGESTION_KEYS if k in entry}


def _suggestion_description(row: dict[str, Any]) -> str:
    return skill_description_text(row)


def _deterministic_reason(description: str, *, slug: str, ctx: str) -> str:
    if description:
        return description
    if _matches_coding_session_start(ctx):
        return f"Recommended for coding session ({slug})"
    return f"Skill {slug}"


def _fetch_candidates(conn: sqlite3.Connection, agent: str) -> list[dict[str, Any]]:
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


def _score_candidate(
    row: dict[str, Any],
    ctx_tokens: set[str],
    normalized_context: str,
    *,
    related_union: frozenset[str] = frozenset(),
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
    if norm_loaded(slug) in related_union:
        score += _RELATED_SKILLS_BOOST
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
        canonical_agent = canonical_seat_or_422(agent)
        seat_preloaded = (
            list(web_auto_inject_skill_slugs())
            if is_web_seat_slug(canonical_agent)
            else []
        )
        return {
            "suggestions": [],
            "loaded_echo": [],
            "omitted": [],
            "seat_preloaded": seat_preloaded,
            "ranker_status": "skipped_no_context",
            "degraded": False,
            "agent": canonical_agent,
            "count": 0,
            "reason": "insufficient_context",
            "stage_a_candidates": [],
            "degraded_skills": [],
        }

    canonical_agent = canonical_seat_or_422(agent)
    seat_preloaded = (
        list(web_auto_inject_skill_slugs()) if is_web_seat_slug(canonical_agent) else []
    )
    loaded_set = build_loaded_set(loaded) | _seat_preloaded_norm_slugs(agent)
    ctx_tokens = tokenize_text(ctx)
    normalized_context = _normalized_context(ctx)
    conn = cortex_conn()
    try:
        rows = _fetch_candidates(conn, agent)
    finally:
        conn.close()
    related_union = _build_related_union(rows, loaded_set)

    scored_loaded: list[dict[str, Any]] = []
    scored_new: list[dict[str, Any]] = []
    loaded_echo: list[str] = []
    degraded_skills: list[dict[str, Any]] = []

    for row in rows:
        source_uri_val = row.get("source_uri")
        slug = slug_from_source_uri(source_uri_val)
        if not slug:
            degraded_skills.append(
                {
                    "id": str(row.get("id") or ""),
                    "name": str(row.get("name") or ""),
                    "source_uri": source_uri_val,
                    "skill_category": row.get("skill_category") or "",
                    "degraded": True,
                    "reason": "source_uri_null"
                    if not source_uri_val
                    else "source_uri_unparseable",
                }
            )
            continue
        entity_id = str(row.get("id") or "")
        scored = _score_candidate(
            row, ctx_tokens, normalized_context, related_union=related_union
        )
        if scored is None:
            continue
        matched = list(scored.matched_terms)
        trigger_terms = _decode_term_list(row.get("trigger_match_terms_json"))
        envelope = index_envelope_fields(row)
        description = _suggestion_description(row)
        entry = {
            "id": entity_id,
            "slug": slug,
            **envelope,
            "score": scored.score,
            "description": description,
            "trigger_match": matched[:5],
            "reason": _deterministic_reason(description, slug=slug, ctx=ctx),
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
    scored_new = _apply_coding_session_start(scored_new, rows, loaded_set, ctx)
    scored_new.sort(key=_sort_key)
    suggestions = [_public_suggestion(item) for item in scored_new[:limit]]
    omitted = [
        {"slug": item["slug"], "reason": "already_loaded"}
        for item in sorted(scored_loaded, key=_sort_key)
    ]

    return {
        "suggestions": suggestions,
        "loaded_echo": loaded_echo,
        "omitted": omitted,
        "seat_preloaded": seat_preloaded,
        "ranker_status": "disabled",
        "degraded": bool(degraded_skills),
        "agent": canonical_agent,
        "count": len(suggestions),
        "stage_a_candidates": scored_new,
        "degraded_skills": degraded_skills,
    }
