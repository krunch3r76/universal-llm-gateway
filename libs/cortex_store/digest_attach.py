"""Digest attach resolve — entity lookup and candidate search helpers."""

from __future__ import annotations

import re
import sqlite3

from fastapi import HTTPException

from .db import query
from .entity_aliases import resolve_entity_reference
from .trait_vocabulary import NON_LIVE_LIFECYCLE

_SEARCH_HIT_LIMIT = 5
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

_LIVE_LIFECYCLE_SQL = (
    "(lifecycle IS NULL OR lifecycle NOT IN ("
    + ", ".join(f"'{lc}'" for lc in sorted(NON_LIVE_LIFECYCLE | {"dismissed"}))
    + "))"
)


def parse_attach_hint(attach_hint: str) -> tuple[str, str, str]:
    """Return (entity_id, entity_type, display_name) from an attach hint."""
    hint = attach_hint.strip()
    if ":" in hint and not hint.startswith("http"):
        entity_type, name = hint.split(":", 1)
        entity_type = entity_type.strip() or "entity"
        name = name.strip() or hint
        return hint, entity_type, name
    slug = re.sub(r"[^a-z0-9]+", "-", hint.lower()).strip("-") or "unknown"
    return f"entity:{slug}", "entity", hint


def _tokenize_hint(hint: str) -> list[str]:
    """Tokenize attach hints; collapse PG&E-style ampersand brands to a single token."""
    normalized = re.sub(r"([A-Za-z]+)\s*&\s*([A-Za-z]+)", r"\1\2", hint)
    tokens = [t.lower() for t in _TOKEN_RE.findall(normalized) if len(t) >= 2]
    return [t for t in tokens if t not in {"the", "and", "for", "from", "with"}]


def _entity_row_is_live(lifecycle: str | None) -> bool:
    if lifecycle is None:
        return True
    return lifecycle not in (NON_LIVE_LIFECYCLE | {"dismissed"})


def _try_direct_type_slug_id(conn: sqlite3.Connection, attach_hint: str) -> str | None:
    if ":" not in attach_hint or attach_hint.startswith("http"):
        return None
    entity_type, slug = attach_hint.split(":", 1)
    entity_type = entity_type.strip()
    slug = slug.strip()
    if not entity_type or not slug:
        return None
    candidate_id = f"{entity_type}:{slug}"
    rows = query(
        conn,
        f"SELECT id, lifecycle FROM entities WHERE id = ? AND {_LIVE_LIFECYCLE_SQL}",
        (candidate_id,),
    )
    if rows and _entity_row_is_live(rows[0].get("lifecycle")):
        return str(rows[0]["id"])
    return None


def digest_attach_search_hits(
    conn: sqlite3.Connection,
    attach_hint: str,
    *,
    limit: int = _SEARCH_HIT_LIMIT,
) -> list[str]:
    """Tokenized LIKE search on live entity id/name; returns up to *limit* ids."""
    tokens = _tokenize_hint(attach_hint)
    if not tokens:
        return []

    clauses: list[str] = []
    params: list[object] = []
    for token in tokens:
        pattern = f"%{token}%"
        clauses.append("(LOWER(id) LIKE ? OR LOWER(name) LIKE ?)")
        params.extend([pattern, pattern])

    rows = query(
        conn,
        "SELECT id, name, lifecycle FROM entities "
        f"WHERE {_LIVE_LIFECYCLE_SQL} AND ({' OR '.join(clauses)}) "
        "ORDER BY id LIMIT ?",
        (*params, max(limit * 8, 40)),
    )

    _type_pref = ("case:", "account:", "finance:", "person:", "contact:", "org:")

    scored: list[tuple[int, int, str]] = []
    for row in rows:
        if not _entity_row_is_live(row.get("lifecycle")):
            continue
        entity_id = str(row["id"])
        blob = f"{entity_id} {row.get('name') or ''}".lower()
        score = sum(1 for token in tokens if token in blob)
        if score <= 0:
            continue
        type_rank = next(
            (i for i, prefix in enumerate(_type_pref) if entity_id.startswith(prefix)),
            len(_type_pref),
        )
        scored.append((score, -type_rank, entity_id))

    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    hits: list[str] = []
    for _, _, entity_id in scored:
        if entity_id not in hits:
            hits.append(entity_id)
        if len(hits) >= limit:
            break
    return hits


def _pick_unique_hit(attach_hint: str, hits: list[str]) -> str | None:
    if len(hits) == 1:
        return hits[0]
    if len(hits) < 2:
        return None
    if ":" in attach_hint and not attach_hint.startswith("http"):
        type_hint = attach_hint.split(":", 1)[0].strip().lower()
        typed = [h for h in hits if h.lower().startswith(f"{type_hint}:")]
        if len(typed) == 1:
            return typed[0]
    preferred_prefixes = (
        "case:",
        "account:",
        "finance:",
        "person:",
        "contact:",
        "org:",
    )
    preferred = [h for h in hits if h.startswith(preferred_prefixes)]
    if len(preferred) == 1:
        return preferred[0]
    if preferred and hits[0] == preferred[0]:
        top_type = preferred[0].split(":", 1)[0]
        same_type = [h for h in preferred if h.startswith(f"{top_type}:")]
        if len(same_type) == 1:
            return preferred[0]
    return None


def digest_resolve_attach(
    conn: sqlite3.Connection,
    attach_hint: str | None,
) -> tuple[str | None, list[str]]:
    """Resolve attach hint to entity id; return (resolved_id, search_hits)."""
    if not attach_hint:
        return None, []

    try:
        resolved = resolve_entity_reference(
            conn, attach_hint, resolve_aliases=True, label="attach"
        )
        return resolved.entity_id, []
    except HTTPException:
        pass

    direct = _try_direct_type_slug_id(conn, attach_hint)
    if direct:
        return direct, []

    hits = digest_attach_search_hits(conn, attach_hint)
    unique = _pick_unique_hit(attach_hint, hits)
    return unique, hits


__all__ = [
    "digest_attach_search_hits",
    "digest_resolve_attach",
    "parse_attach_hint",
]
