"""Entity discoverability helpers for cortex search, recall, and entity_create.

Mint default aliases from typed ids and match queries against entity name,
description, alias index, and source_uri without requiring assertion rows.
"""

from __future__ import annotations

import re
import sqlite3
import zlib
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from .db import query
from .entity_aliases import live_alias_rows

_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)

_GENERIC_IDENTITY_TYPES = frozenset({"service", "todo"})


def _normalize_tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_SPLIT.split(text.strip()) if len(t) >= 2]


def _token_set(text: str) -> set[str]:
    return set(_normalize_tokens(text))


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


def _slug_from_entity_id(entity_id: str) -> str:
    if ":" in entity_id:
        return entity_id.split(":", 1)[1]
    return entity_id


def _aliases_for_entity(
    entity_id: str,
    row: dict[str, Any],
    aliases_by_entity: dict[str, list[str]],
) -> list[str]:
    out: list[str] = list(aliases_by_entity.get(entity_id, []))
    raw_aliases = row.get("aliases")
    if isinstance(raw_aliases, list):
        for alias in raw_aliases:
            text = str(alias).strip()
            if text and text not in out:
                out.append(text)
    return out


def _query_tokens_subset_of(field_tokens: set[str], query_tokens: list[str]) -> bool:
    if not query_tokens:
        return False
    return set(query_tokens) <= field_tokens


def _single_token_identity_match(
    token: str,
    *,
    entity_id: str,
    name: str,
    aliases: list[str],
) -> bool:
    slug = _slug_from_entity_id(entity_id).lower()
    if token == slug or token == entity_id.lower():
        return True
    if token == name.lower():
        return True
    for alias in aliases:
        if token == str(alias).lower():
            return True
    return False


def _identity_field_match(
    query_tokens: list[str],
    *,
    entity_id: str,
    name: str,
    aliases: list[str],
    source_uri: str,
) -> bool:
    """True when all query tokens appear as whole tokens in one identity field."""
    if not query_tokens:
        return False

    if len(query_tokens) == 1:
        return _single_token_identity_match(
            query_tokens[0],
            entity_id=entity_id,
            name=name,
            aliases=aliases,
        )

    name_tokens = _token_set(name)
    if _query_tokens_subset_of(name_tokens, query_tokens):
        return True

    slug = _slug_from_entity_id(entity_id)
    slug_tokens = _token_set(slug.replace("-", " ").replace("_", " "))
    slug_tokens |= _token_set(slug)
    if _query_tokens_subset_of(slug_tokens, query_tokens):
        return True

    for alias in aliases:
        if _query_tokens_subset_of(_token_set(alias), query_tokens):
            return True

    uri_parts = _source_uri_identity_fragments(source_uri)
    for fragment in uri_parts:
        if _query_tokens_subset_of(_token_set(fragment), query_tokens):
            return True

    combined = name_tokens | slug_tokens
    if _query_tokens_subset_of(combined, query_tokens):
        return True

    return False


def _identity_match_and_score(
    query_tokens: list[str],
    *,
    entity_id: str,
    name: str,
    aliases: list[str],
    source_uri: str,
    description: str,
) -> tuple[bool, float, float]:
    """Return (identity_match, combmax_score, identity_specificity)."""
    if not query_tokens:
        return False, 0.0, 0.0

    query_set = set(query_tokens)
    desc_tokens = _token_set(description)
    name_tokens = _token_set(name)
    slug_tokens = _token_set(_slug_from_entity_id(entity_id).replace("-", " ").replace("_", " "))
    slug_tokens |= _token_set(_slug_from_entity_id(entity_id))

    full_token_union = set(name_tokens)
    full_token_union |= slug_tokens
    full_token_union |= desc_tokens
    for alias in aliases:
        full_token_union |= _token_set(alias)
    for fragment in _source_uri_identity_fragments(source_uri):
        full_token_union |= _token_set(fragment)

    hits_full = len(query_set & full_token_union)
    if hits_full == 0:
        return False, 0.0, 0.0

    identity_match = _identity_field_match(
        query_tokens,
        entity_id=entity_id,
        name=name,
        aliases=aliases,
        source_uri=source_uri,
    )

    if identity_match:
        identity_tokens = name_tokens | slug_tokens
        for alias in aliases:
            identity_tokens |= _token_set(alias)
        for fragment in _source_uri_identity_fragments(source_uri):
            identity_tokens |= _token_set(fragment)
        specificity = len(query_tokens) / max(len(identity_tokens), 1)
        return True, 1.0, specificity

    if len(query_tokens) == 1:
        return False, 0.0, 0.0

    if hits_full == len(query_tokens):
        return False, 0.88, 0.0

    partial = min(0.55, 0.25 + 0.15 * hits_full)
    if partial >= 0.85:
        partial = 0.84
    return False, partial, 0.0


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


def _group_aliases_by_entity(
    alias_rows: list[tuple[str, str, str]],
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for ent_id, _etype, alias in alias_rows:
        grouped.setdefault(ent_id, []).append(str(alias))
    return grouped


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
    aliases_by_entity = _group_aliases_by_entity(alias_index)
    scored: list[tuple[tuple[float, float, int], dict[str, Any]]] = []
    for row in rows:
        lifecycle = row.get("lifecycle")
        if lifecycle in ("merged", "deprecated", "reaped"):
            continue
        eid = str(row["id"])
        name = str(row.get("name") or "")
        source_uri = str(row.get("source_uri") or "")
        entity_aliases = _aliases_for_entity(eid, row, aliases_by_entity)
        description = str(row.get("description") or "")
        identity_match, score, specificity = _identity_match_and_score(
            tokens,
            entity_id=eid,
            name=name,
            aliases=entity_aliases,
            source_uri=source_uri,
            description=description,
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
    digest = zlib.crc32(entity_id.encode("utf-8"))
    return -(abs(digest) % (2**30)) - 1


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
    identity_hits.sort(key=_entity_identity_sort_key, reverse=True)
    top_identity = [
        h
        for h in identity_hits
        if float(h.get("combmax_score") or 0.0) >= 1.0
    ][:2]

    strong_whole_token = [
        h
        for h in entity_hits
        if not h.get("identity_match")
        and float(h.get("combmax_score") or 0.0) >= 0.88
    ]
    strong_whole_token.sort(key=_entity_identity_sort_key, reverse=True)

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

    for hit in strong_whole_token:
        eid = str(hit["entity_id"])
        if eid in seen_entities:
            continue
        score = float(hit.get("combmax_score") or 0.0)
        if score < 0.88:
            continue
        row = entity_hit_to_search_row(hit, q=q)
        merged.append(row)
        seen_entities.add(eid)

    return merged[:limit]
