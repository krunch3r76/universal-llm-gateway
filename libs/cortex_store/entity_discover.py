"""Entity discoverability helpers for cortex search, recall, and entity_create.

Mint default aliases from typed ids and match queries against entity name,
description, alias index, and source_uri without requiring assertion rows.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from .db import query
from .entity_aliases import live_alias_rows

_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)

_GENERIC_IDENTITY_TYPES = frozenset({"service", "todo"})


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


def _source_uri_identity_fragments(source_uri: str) -> list[str]:
    if not source_uri.strip():
        return []
    parsed = urlparse(source_uri)
    path = parsed.path if parsed.scheme else source_uri
    path = path.lstrip("/")
    if not path:
        return []
    base = PurePosixPath(path).name
    stem = PurePosixPath(base).stem
    out = [base, stem]
    if stem.endswith(".extracted"):
        stem = PurePosixPath(stem).stem
        out.append(stem)
    for piece in (base, stem):
        out.append(piece.replace("-", " ").replace("_", " "))
    return out


def _entity_identity_text(row: dict[str, Any], alias_rows: list[tuple[str, str, str]]) -> str:
    eid = str(row.get("id") or "")
    parts = [eid]
    if ":" in eid:
        slug = eid.split(":", 1)[1]
        parts.extend([slug, slug.replace("-", " ").replace("_", " ")])
    parts.append(str(row.get("name") or ""))
    raw_aliases = row.get("aliases")
    if isinstance(raw_aliases, list):
        parts.extend(str(alias) for alias in raw_aliases)
    for ent_id, _etype, alias in alias_rows:
        if ent_id == eid:
            parts.append(alias)
    parts.extend(_source_uri_identity_fragments(str(row.get("source_uri") or "")))
    return " ".join(p for p in parts if p).lower()


def _single_token_identity_match(
    token: str,
    *,
    entity_id: str,
    name: str,
    alias_rows: list[tuple[str, str, str]],
) -> bool:
    slug = entity_id.split(":", 1)[-1].lower() if ":" in entity_id else entity_id.lower()
    if token == slug or token == entity_id.lower():
        return True
    if token == name.lower():
        return True
    for ent_id, _etype, alias in alias_rows:
        if ent_id == entity_id and token == str(alias).lower():
            return True
    return False


def _identity_match_and_score(
    query_tokens: list[str],
    *,
    identity_text: str,
    full_text: str,
    entity_id: str,
    name: str,
    alias_rows: list[tuple[str, str, str]],
) -> tuple[bool, float, float]:
    """Return (identity_match, combmax_score, identity_specificity)."""
    if not query_tokens:
        return False, 0.0, 0.0

    identity_tokens = set(_normalize_tokens(identity_text))
    hits_identity = sum(1 for tok in query_tokens if tok in identity_text)
    hits_full = sum(1 for tok in query_tokens if tok in full_text)
    if hits_full == 0:
        return False, 0.0, 0.0
    if hits_full < len(query_tokens):
        partial = min(0.55, 0.25 + 0.15 * hits_full)
        return False, partial, 0.0

    if hits_identity == len(query_tokens):
        if len(query_tokens) >= 2 or _single_token_identity_match(
            query_tokens[0],
            entity_id=entity_id,
            name=str(name or ""),
            alias_rows=alias_rows,
        ):
            specificity = len(query_tokens) / max(len(identity_tokens), 1)
            return True, 1.0, specificity
    if hits_full == len(query_tokens):
        return False, 0.88, 0.0
    return False, min(0.55, 0.25 + 0.15 * hits_full), 0.0


def _entity_identity_sort_key(hit: dict[str, Any]) -> tuple[float, float, int]:
    entity_type = str(hit.get("entity_type") or "")
    type_rank = 0
    if entity_type == "document":
        type_rank = 3
    elif entity_type not in _GENERIC_IDENTITY_TYPES:
        type_rank = 2
    elif entity_type == "todo":
        type_rank = 1
    return (
        float(hit.get("combmax_score") or 0.0),
        float(hit.get("identity_specificity") or 0.0),
        type_rank,
    )


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
    scored: list[tuple[tuple[float, float, int], dict[str, Any]]] = []
    for row in rows:
        lifecycle = row.get("lifecycle")
        if lifecycle in ("merged", "deprecated", "reaped"):
            continue
        eid = str(row["id"])
        name = str(row.get("name") or "")
        identity_text = _entity_identity_text(row, alias_index)
        description = str(row.get("description") or "").lower()
        full_text = f"{identity_text} {description}".strip()
        identity_match, score, specificity = _identity_match_and_score(
            tokens,
            identity_text=identity_text,
            full_text=full_text,
            entity_id=eid,
            name=name,
            alias_rows=alias_index,
        )
        if score <= 0:
            continue
        hit = {
            "entity_id": eid,
            "entity_name": row.get("name"),
            "entity_type": row.get("type"),
            "combmax_score": round(score, 4),
            "retrieval_source": "entity",
            "identity_match": identity_match,
            "identity_specificity": round(specificity, 4),
        }
        scored.append((_entity_identity_sort_key(hit), hit))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _key, item in scored[:limit]]


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

    identity_hits = [h for h in entity_hits if h.get("identity_match")]
    other_entity_hits = [h for h in entity_hits if not h.get("identity_match")]
    identity_hits.sort(key=_entity_identity_sort_key, reverse=True)
    top_identity = [
        h
        for h in identity_hits
        if float(h.get("combmax_score") or 0.0) >= 1.0
    ][:2]

    top_assertion = max(
        (float(h.get("combmax_score") or 0.0) for h in assertion_hits),
        default=0.0,
    )

    merged: list[dict[str, Any]] = []
    front_entities: set[str] = set()

    for hit in top_identity:
        eid = str(hit["entity_id"])
        merged.append(entity_hit_to_search_row(hit, q=q))
        front_entities.add(eid)

    for row in assertion_hits:
        eid = str(row.get("entity_id") or "")
        if eid and eid in front_entities:
            continue
        merged.append(row)

    seen_entities = front_entities | {
        str(h.get("entity_id")) for h in merged if h.get("entity_id")
    }

    for hit in other_entity_hits:
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

    return merged[:limit]
