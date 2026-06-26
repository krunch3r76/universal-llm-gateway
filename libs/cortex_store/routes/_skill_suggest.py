"""Frozen Stage-A deterministic skill suggestion engine (todo:skill-suggest-mcp-tool §3)."""

from __future__ import annotations

import json
from typing import Any

from agent_seat.body_injection import is_web_seat_slug
from agent_seat.inject_channels import web_seat_injected_skill_slugs
from agent_seat.inject_registry import (
    CODING_SESSION_ADVERTISE_SLUGS,
    coding_scope_inject_entity_ids,
    injected_skill_slugs,
)

from ..db import cortex_conn
from ..seat_applicability import canonical_seat_or_422
from ._skill_index import index_envelope_fields
from ._skill_suggest_candidates import (
    STOPWORDS,
    build_extended_candidate,
    build_related_union,
    fetch_candidates,
    norm_loaded,
    normalized_context,
    passes_precision_gate,
    row_applies_to_seat,
    score_candidate,
    slug_from_source_uri,
    sort_key,
    suggestion_description,
    tokenize_text,
)

__all__ = [
    "STOPWORDS",
    "build_loaded_set",
    "norm_loaded",
    "run_stage_a",
    "slug_from_source_uri",
    "tokenize_text",
]

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


def _coding_session_bundle_slugs() -> frozenset[str]:
    inject = tuple(
        entity_id.removeprefix("agent_skill:")
        for entity_id in coding_scope_inject_entity_ids()
    )
    return frozenset((*inject, *CODING_SESSION_ADVERTISE_SLUGS))


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


def build_loaded_set(loaded: list[str]) -> set[str]:
    return {norm_loaded(x) for x in loaded if isinstance(x, str) and x.strip()}


def _seat_platform(agent: str) -> str:
    parts = agent.split("-", 1)
    return parts[1] if len(parts) == 2 else "web"


def _seat_preloaded_norm_slugs(agent: str) -> frozenset[str]:
    """Web inject channels 1–3 merged into loaded_set (Slice F + boot inline)."""
    canonical = canonical_seat_or_422(agent)
    if not is_web_seat_slug(canonical):
        return frozenset()
    parts = canonical.split("-", 1)
    family, platform = (parts[0], parts[1]) if len(parts) == 2 else ("", "")
    return frozenset(
        norm_loaded(slug)
        for slug in web_seat_injected_skill_slugs(
            canonical, family=family, platform=platform
        )
    )


def _is_loaded(candidate_slug: str, entity_id: str, loaded_set: set[str]) -> bool:
    slug_norm = norm_loaded(candidate_slug)
    id_norm = norm_loaded(entity_id)
    return (
        slug_norm in loaded_set
        or id_norm in loaded_set
        or norm_loaded(f"agent_skill:{candidate_slug}") in loaded_set
    )


def _public_suggestion(entry: dict[str, Any]) -> dict[str, Any]:
    """Agent-facing suggestion shape — descriptions, not tag evidence."""
    return {k: entry[k] for k in _PUBLIC_SUGGESTION_KEYS if k in entry}


def _humanize_slug(slug: str) -> str:
    from ._skill_suggest_candidates import humanize_slug

    return humanize_slug(slug)


def _deterministic_reason(description: str, *, slug: str, ctx: str) -> str:
    if description:
        return description
    if _matches_coding_session_start(ctx):
        return f"Recommended for coding session ({slug})"
    return f"Skill {slug}"


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


def _apply_coding_session_start(
    scored_new: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    loaded_set: set[str],
    ctx: str,
    *,
    agent: str,
) -> list[dict[str, Any]]:
    if not _matches_coding_session_start(ctx):
        return scored_new

    canonical_agent = canonical_seat_or_422(agent)
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
        if not row_applies_to_seat(row, canonical_agent):
            continue
        entity_id = str(row.get("id") or "")
        if _is_loaded(slug, entity_id, loaded_set):
            continue
        trigger_terms = _decode_term_list(row.get("trigger_match_terms_json"))
        envelope = index_envelope_fields(row)
        description = suggestion_description(row)
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


def _build_extended_candidates(
    rows: list[dict[str, Any]],
    ctx_tokens: set[str],
    normalized_ctx: str,
    *,
    related_union: frozenset[str],
) -> list[dict[str, Any]]:
    extended: list[dict[str, Any]] = []
    for row in rows:
        scored = score_candidate(
            row, ctx_tokens, normalized_ctx, related_union=related_union
        )
        entry = build_extended_candidate(row, scored)
        if entry is not None:
            extended.append(entry)
    extended.sort(key=sort_key)
    return extended


def run_stage_a(
    *,
    agent: str,
    loaded: list[str],
    conversation_context: str | None,
    limit: int,
    return_all_candidates: bool = False,
) -> dict[str, Any]:
    """Pure deterministic Stage-A engine (§3)."""
    ctx = (conversation_context or "").strip()
    if not ctx:
        canonical_agent = canonical_seat_or_422(agent)
        seat_preloaded = (
            list(
                injected_skill_slugs(
                    role=canonical_agent,
                    platform=_seat_platform(canonical_agent),
                )
            )
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
        list(
            injected_skill_slugs(
                role=canonical_agent,
                platform=_seat_platform(canonical_agent),
            )
        )
        if is_web_seat_slug(canonical_agent)
        else []
    )
    explicit_loaded_set = build_loaded_set(loaded)
    loaded_set = explicit_loaded_set | _seat_preloaded_norm_slugs(agent)
    ctx_tokens = tokenize_text(ctx)
    normalized_ctx = normalized_context(ctx)
    conn = cortex_conn()
    try:
        rows = fetch_candidates(conn, agent)
    finally:
        conn.close()
    related_union = build_related_union(rows, explicit_loaded_set)

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
        scored = score_candidate(
            row, ctx_tokens, normalized_ctx, related_union=related_union
        )
        if scored is None:
            continue
        matched = list(scored.matched_terms)
        trigger_terms = _decode_term_list(row.get("trigger_match_terms_json"))
        envelope = index_envelope_fields(row)
        description = suggestion_description(row)
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
        elif passes_precision_gate(scored):
            scored_new.append(entry)

    loaded_echo = sorted(set(loaded_echo))
    scored_new = _apply_coding_session_start(
        scored_new, rows, loaded_set, ctx, agent=agent
    )
    scored_new.sort(key=sort_key)
    suggestions = [_public_suggestion(item) for item in scored_new[:limit]]
    omitted = [
        {"slug": item["slug"], "reason": "already_loaded"}
        for item in sorted(scored_loaded, key=sort_key)
    ]

    result: dict[str, Any] = {
        "suggestions": suggestions,
        "loaded_echo": loaded_echo,
        "omitted": omitted,
        "seat_preloaded": seat_preloaded,
        "ranker_status": "deterministic_fallback",
        "degraded": bool(degraded_skills),
        "agent": canonical_agent,
        "count": len(suggestions),
        "stage_a_candidates": scored_new,
        "degraded_skills": degraded_skills,
    }
    if return_all_candidates:
        result["stage_a_extended_candidates"] = _build_extended_candidates(
            rows, ctx_tokens, normalized_ctx, related_union=related_union
        )
    return result
