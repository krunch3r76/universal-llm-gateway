"""Entity discoverability helpers for cortex search, recall, and entity_create.

Mint default aliases from typed ids and match queries against entity name,
description, alias index, and source_uri without requiring assertion rows.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .db import query
from .entity_aliases import live_alias_rows

_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def _normalize_tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_SPLIT.split(text.strip()) if len(t) >= 2]


def mint_entity_create_aliases(
    entity_id: str,
    name: str | None,
    caller_aliases: list[str] | None,
) -> list[str]:
    """Merge caller aliases with id/slug/name forms (deduped, stable order)."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        text = str(value).strip()
        if not text or text in seen:
            return
        seen.add(text)
        ordered.append(text)

    if caller_aliases:
        for alias in caller_aliases:
            add(alias)

    add(entity_id)
    if ":" in entity_id:
        slug = entity_id.split(":", 1)[1]
        add(slug)
        add(slug.replace("-", " ").replace("_", " "))
    if name:
        add(name)
        norm_name = " ".join(name.split())
        if norm_name != name:
            add(norm_name)
    return ordered


def _entity_searchable_text(row: dict[str, Any], alias_rows: list[tuple[str, str, str]]) -> str:
    eid = str(row.get("id") or "")
    parts = [
        eid,
        str(row.get("name") or ""),
        str(row.get("description") or ""),
        str(row.get("source_uri") or ""),
    ]
    for ent_id, _etype, alias in alias_rows:
        if ent_id == eid:
            parts.append(alias)
    return " ".join(parts).lower()


def _score_entity_match(query_tokens: list[str], haystack: str) -> float:
    if not query_tokens:
        return 0.0
    hits = sum(1 for tok in query_tokens if tok in haystack)
    if hits == 0:
        return 0.0
    if hits == len(query_tokens):
        return 0.96 if len(query_tokens) >= 2 else 0.88
    return min(0.55, 0.25 + 0.15 * hits)


def search_entities_by_query(
    conn: sqlite3.Connection,
    q: str,
    *,
    entity_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Rank live entities by token overlap on name, description, aliases, source_uri."""
    tokens = _normalize_tokens(q)
    if not tokens:
        return []

    clauses = ["1=1"]
    params: list[Any] = []
    if entity_type:
        clauses.append("type = ?")
        params.append(entity_type)

    sql = (
        "SELECT id, type, name, description, source_uri, aliases, lifecycle "
        f"FROM entities WHERE {' AND '.join(clauses)}"
    )
    rows = query(conn, sql, tuple(params))
    alias_index = live_alias_rows(conn)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        lifecycle = row.get("lifecycle")
        if lifecycle in ("merged", "deprecated", "reaped"):
            continue
        haystack = _entity_searchable_text(row, alias_index)
        score = _score_entity_match(tokens, haystack)
        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    "entity_id": str(row["id"]),
                    "entity_name": row.get("name"),
                    "entity_type": row.get("type"),
                    "combmax_score": round(score, 4),
                    "retrieval_source": "entity",
                },
            )
        )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored[:limit]]


def entity_synthetic_assertion_id(entity_id: str) -> int:
    """Stable negative id for entity-only search hits (not a real assertion row)."""
    return -(abs(hash(entity_id)) % (2**30)) - 1


def entity_hit_to_search_row(entity_hit: dict[str, Any], *, q: str) -> dict[str, Any]:
    """Shape an entity match as an assertion search fused row."""
    eid = str(entity_hit["entity_id"])
    name = str(entity_hit.get("entity_name") or eid)
    return {
        "id": entity_synthetic_assertion_id(eid),
        "entity_id": eid,
        "entity_name": entity_hit.get("entity_name"),
        "claim": name,
        "confidence": "believed",
        "review_status": None,
        "combmax_score": entity_hit.get("combmax_score", 0.0),
        "retrieval_source": "entity",
        "rank": None,
        "created_at": "",
        "seeded_by": "entity_discover",
        "match_query": q,
    }


def merge_entity_seed_hits(
    assertion_hits: list[dict[str, Any]],
    entity_hits: list[dict[str, Any]],
    *,
    q: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Fuse assertion CombMAX hits with entity-field matches without displacing strong FTS."""
    if not entity_hits:
        return assertion_hits[:limit]

    top_assertion = max(
        (float(h.get("combmax_score") or 0.0) for h in assertion_hits),
        default=0.0,
    )
    merged = list(assertion_hits)
    seen_entities = {str(h.get("entity_id")) for h in merged if h.get("entity_id")}

    for hit in entity_hits:
        eid = str(hit["entity_id"])
        if eid in seen_entities:
            continue
        score = float(hit.get("combmax_score") or 0.0)
        if top_assertion >= 0.75 and score < top_assertion + 0.05:
            continue
        if top_assertion >= 0.5 and score < 0.85:
            continue
        row = entity_hit_to_search_row(hit, q=q)
        merged.append(row)
        seen_entities.add(eid)

    merged.sort(key=lambda item: float(item.get("combmax_score") or 0.0), reverse=True)
    return merged[:limit]
