"""Advisory write-discipline nudges at assert / entity_create dispatch paths.

WARN/advise only — never blocks writes. Complements post-insert near_dup in
``routes/assertions/_create.py`` and post-create ``collision_warning`` from
``entity_collision.py``.

Anchored on ``decision:session-close-edge-first-enrichment`` (edge-first, route
facts to child entities instead of hub assertion bloat).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .belief_guard import (
    SUPERSEDE_COSINE_THRESHOLD,
    TOUCHED_COSINE_THRESHOLD,
    analyze_assertion_impact,
)
from .db import query

_CONTAINER_TYPES = frozenset({"task", "project", "plan", "decision"})
_LEAF_CREATE_TYPES = frozenset({"todo", "plan_phase", "finding", "friction"})
_HUB_ASSERTION_THRESHOLD = 12
_SPARSE_EDGE_ASSERTION_THRESHOLD = 3


def _entity_graph_stats(
    conn: sqlite3.Connection, entity_id: str
) -> tuple[str | None, int, int]:
    """Return (entity_type, active_assertion_count, active_relationship_count)."""
    rows = query(
        conn,
        "SELECT type FROM entities WHERE id = ?",
        (entity_id,),
    )
    entity_type = rows[0]["type"] if rows else None

    a_rows = query(
        conn,
        "SELECT COUNT(*) AS cnt FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL",
        (entity_id,),
    )
    assertion_count = int(a_rows[0]["cnt"]) if a_rows else 0

    r_rows = query(
        conn,
        "SELECT COUNT(*) AS cnt FROM relationships "
        "WHERE active = 1 AND (from_entity = ? OR to_entity = ?)",
        (entity_id, entity_id),
    )
    relationship_count = int(r_rows[0]["cnt"]) if r_rows else 0
    return entity_type, assertion_count, relationship_count


def build_assert_nudge(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
    confidence: str,
    predicate_form: str | None = None,
) -> dict[str, Any] | None:
    """Pre-write advisory for assert — dedup hint + child-entity routing."""
    entity_type, assertion_count, relationship_count = _entity_graph_stats(
        conn, entity_id
    )
    reasons: list[str] = []
    suggestions: list[str] = []
    analyze_hint: dict[str, Any] = {}

    impact = analyze_assertion_impact(
        conn, entity_id, claim, confidence, predicate_form=predicate_form
    )
    if impact.likely_supersedes:
        reasons.append("likely_reassertion")
        ids = impact.likely_supersedes[:5]
        suggestions.append(
            "similar claim(s) already on entity — prefer supersede(...) or "
            f"analyze_impact before assert (likely_supersedes={ids})"
        )
        analyze_hint["likely_supersedes"] = ids
    elif impact.touched_assertions:
        top = impact.touched_assertions[0]
        if top.similarity >= TOUCHED_COSINE_THRESHOLD:
            reasons.append("similar_existing_claims")
            suggestions.append(
                f"{len(impact.touched_assertions)} similar claim(s) on entity "
                f"(top sim={top.similarity:.2f}, assertion #{top.assertion_id}) — "
                "call analyze_impact before assert"
            )
            analyze_hint["touched_count"] = len(impact.touched_assertions)
            analyze_hint["top_similarity"] = top.similarity

    if entity_type in _CONTAINER_TYPES:
        if relationship_count == 0 and assertion_count >= _SPARSE_EDGE_ASSERTION_THRESHOLD:
            reasons.append("sparse_edges_hub_bloat")
            suggestions.append(
                "edge-first (decision:session-close-edge-first-enrichment): "
                "relationship_create before more assertions; route new facts to a "
                "child entity (entity_create + child_of)"
            )
        elif assertion_count >= _HUB_ASSERTION_THRESHOLD:
            reasons.append("high_assertion_density")
            suggestions.append(
                "hub entity — consider entity_create for a child todo/decision "
                "and relationship_create child_of instead of asserting here"
            )

    if not reasons:
        return None

    return {
        "level": "warn",
        "reasons": reasons,
        "message": "write-discipline advisory (non-blocking)",
        "suggestions": suggestions,
        "entity_stats": {
            "entity_type": entity_type,
            "assertion_count": assertion_count,
            "relationship_count": relationship_count,
        },
        **({"analyze_impact": analyze_hint} if analyze_hint else {}),
        "_thresholds": {
            "supersede_similarity": SUPERSEDE_COSINE_THRESHOLD,
            "touched_similarity": TOUCHED_COSINE_THRESHOLD,
        },
    }


def build_entity_create_nudge(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    name: str,
    description: str | None,
) -> dict[str, Any] | None:
    """Pre-write advisory for entity_create — read-first + child routing."""
    reasons: list[str] = ["read_first"]
    suggestions: list[str] = [
        "read-first: search(query=...) or entities(type=..., query=...) before "
        "entity_create to avoid near-duplicate slugs (exact-slug 409 still applies)"
    ]

    if entity_type in _LEAF_CREATE_TYPES:
        reasons.append("child_routing")
        suggestions.append(
            f"after create, wire relationship_create child_of to the parent "
            f"task/plan/project — leaf {entity_type!r} should not float orphaned"
        )

    if entity_type in _CONTAINER_TYPES:
        reasons.append("container_create")
        suggestions.append(
            "container entity — seed with relationship_create edges first, "
            "then assert on child leaf entities (todo/decision), not on the hub"
        )

    query_seed = (name or "")[:80].strip()
    if description and len(description) > 20:
        query_seed = f"{query_seed} {description[:60]}".strip()

    return {
        "level": "warn",
        "reasons": reasons,
        "message": "write-discipline advisory (non-blocking)",
        "suggestions": suggestions,
        "read_first": {
            "recommended_ops": [
                f'search(query="{query_seed}")' if query_seed else "search(query=...)",
                f'entities(type="{entity_type}", query="{query_seed}")'
                if query_seed
                else f'entities(type="{entity_type}", query=...)',
            ],
        },
    }


def attach_write_discipline(result: dict[str, Any], nudge: dict[str, Any]) -> None:
    """Merge advisory nudge into a successful dispatch result."""
    result["write_discipline"] = nudge
    summary = "; ".join(nudge.get("suggestions", [])[:2])
    if not summary:
        return
    prefix = "write_discipline (advisory): "
    if "_next" in result:
        result["_next"] = f"{result['_next']}; {prefix}{summary}"
    else:
        result["_next"] = f"{prefix}{summary}"
