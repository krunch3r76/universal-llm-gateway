"""entities_fts — FTS5 index sync, drift probe, and entity search (S1)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import query
from .entity_aliases import live_alias_rows
from .entity_discover import (
    _aliases_for_entity,
    _group_aliases_by_entity,
    _identity_match_and_score,
    _normalize_tokens,
    _slug_from_entity_id,
    _source_uri_identity_fragments,
)

_BM25_WEIGHTS = (8.0, 6.0, 6.0, 4.0, 1.0)
_STRICT_IDENTITY_COLS = "{name aliases slug src_fname}"
_LOOSE_THRESHOLD = 5
_STRICT_FETCH_CAP = 50

_TYPE_PRIOR: dict[str, float] = {
    "document": 1.0,
    "person": 0.9,
    "organization": 0.9,
    "account": 0.9,
    "decision": 0.9,
    "legal_matter": 0.9,
    "thread": 0.6,
    "todo": 0.5,
    "transcript": 0.4,
    "test": 0.3,
}
_DEFAULT_TYPE_PRIOR = 0.85


def _sanitize_fts_query(raw: str) -> str:
    tokens = raw.strip().split()
    if not tokens:
        return ""
    return " ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens)


def entities_fts_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities_fts'"
    ).fetchone()
    return row is not None


def _type_prior(etype: str) -> float:
    return _TYPE_PRIOR.get(etype, _DEFAULT_TYPE_PRIOR)


def _slug_index_text(entity_id: str) -> str:
    slug = _slug_from_entity_id(entity_id)
    parts = [slug]
    spaced = slug.replace("-", " ").replace("_", " ")
    if spaced != slug:
        parts.append(spaced)
    return " ".join(parts)


def _src_fname_index_text(source_uri: str | None) -> str:
    if not source_uri:
        return ""
    return " ".join(_source_uri_identity_fragments(str(source_uri)))


def _aliases_index_text(
    conn: sqlite3.Connection,
    entity_id: str,
    name: str | None,
    row_aliases: object,
) -> str:
    parts: list[str] = []
    seen: set[str] = set()

    def add(text: str | None) -> None:
        if not text:
            return
        t = str(text).strip()
        if not t or t in seen:
            return
        seen.add(t)
        parts.append(t)

    if name:
        add(name)
        add(" ".join(name.split()))

    if isinstance(row_aliases, list):
        for alias in row_aliases:
            add(str(alias))
    elif isinstance(row_aliases, str) and row_aliases.strip():
        try:
            parsed = json.loads(row_aliases)
            if isinstance(parsed, list):
                for alias in parsed:
                    add(str(alias))
        except (json.JSONDecodeError, TypeError):
            add(row_aliases)

    for eid, _etype, alias in live_alias_rows(conn):
        if eid == entity_id:
            add(alias)

    return " ".join(parts)


def reindex_entity_fts_conn(conn: sqlite3.Connection, entity_id: str) -> None:
    """Rebuild one entities_fts row in the caller's transaction (no commit)."""
    if not entities_fts_table_exists(conn):
        return
    row = conn.execute(
        "SELECT id, type, name, description, source_uri, aliases, lifecycle "
        "FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if not row:
        conn.execute("DELETE FROM entities_fts WHERE entity_id = ?", (entity_id,))
        return
    eid, etype, name, description, source_uri, aliases, lifecycle = row
    if lifecycle in ("merged", "deprecated", "reaped"):
        conn.execute("DELETE FROM entities_fts WHERE entity_id = ?", (entity_id,))
        return
    conn.execute("DELETE FROM entities_fts WHERE entity_id = ?", (entity_id,))
    conn.execute(
        "INSERT INTO entities_fts "
        "(name, aliases, slug, src_fname, description, entity_id, etype) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            name or "",
            _aliases_index_text(conn, str(eid), name, aliases),
            _slug_index_text(str(eid)),
            _src_fname_index_text(source_uri),
            description or "",
            str(eid),
            str(etype),
        ),
    )


def backfill_entities_fts_conn(conn: sqlite3.Connection) -> int:
    """Backfill live entities into entities_fts. Idempotent per entity."""
    if not entities_fts_table_exists(conn):
        return 0
    rows = conn.execute(
        "SELECT id FROM entities WHERE lifecycle IS NULL "
        "OR lifecycle NOT IN ('merged', 'deprecated', 'reaped')"
    ).fetchall()
    for (entity_id,) in rows:
        reindex_entity_fts_conn(conn, str(entity_id))
    return len(rows)


def entities_fts_drift(conn: sqlite3.Connection) -> tuple[int, int]:
    """Return (live_entity_count, entities_fts_row_count) for drift probes."""
    if not entities_fts_table_exists(conn):
        live = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE lifecycle IS NULL "
            "OR lifecycle NOT IN ('merged', 'deprecated', 'reaped')"
        ).fetchone()[0]
        return int(live), 0
    live = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE lifecycle IS NULL "
        "OR lifecycle NOT IN ('merged', 'deprecated', 'reaped')"
    ).fetchone()[0]
    fts = conn.execute("SELECT COUNT(*) FROM entities_fts").fetchone()[0]
    return int(live), int(fts)


def _bm25_expr() -> str:
    w = ", ".join(str(x) for x in _BM25_WEIGHTS)
    return f"bm25(entities_fts, {w})"


def _fetch_fts_lane(
    conn: sqlite3.Connection,
    match_query: str,
    *,
    entity_type: str | None,
    cap: int,
) -> list[dict[str, Any]]:
    clauses = ["entities_fts MATCH ?"]
    params: list[Any] = [match_query]
    if entity_type:
        clauses.append("etype = ?")
        params.append(entity_type)
    sql = (
        f"SELECT entity_id, etype, name, {_bm25_expr()} AS rank "
        f"FROM entities_fts WHERE {' AND '.join(clauses)} "
        "ORDER BY rank LIMIT ?"
    )
    params.append(cap)
    return query(conn, sql, tuple(params))


def search_entities_fts(
    conn: sqlite3.Connection,
    q: str,
    *,
    entity_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Strict-then-loose entities_fts MATCH + bm25; no full-table entity scan."""
    tokens = _normalize_tokens(q)
    if not tokens:
        return []
    if not entities_fts_table_exists(conn):
        return []
    fts_count = conn.execute("SELECT COUNT(*) FROM entities_fts").fetchone()[0]
    if not fts_count:
        return []

    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return []

    strict_match = f"{_STRICT_IDENTITY_COLS}: {sanitized}"
    strict_rows = _fetch_fts_lane(
        conn, strict_match, entity_type=entity_type, cap=_STRICT_FETCH_CAP
    )
    rows: list[dict[str, Any]] = list(strict_rows)
    seen_ids = {str(r["entity_id"]) for r in strict_rows}

    if len(strict_rows) < _LOOSE_THRESHOLD:
        loose_rows = _fetch_fts_lane(
            conn, sanitized, entity_type=entity_type, cap=_STRICT_FETCH_CAP
        )
        for row in loose_rows:
            eid = str(row["entity_id"])
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            rows.append(row)

    if not rows:
        return []

    max_abs_rank = max((abs(float(r.get("rank") or 0.0)) for r in rows), default=1.0)
    if max_abs_rank <= 0:
        max_abs_rank = 1.0

    alias_index = live_alias_rows(conn)
    aliases_by_entity = _group_aliases_by_entity(alias_index)
    entity_rows = {
        str(r["id"]): r
        for r in query(
            conn,
            f"SELECT id, type, name, description, source_uri, aliases, lifecycle "
            f"FROM entities WHERE id IN ({','.join('?' * len(seen_ids))})",
            tuple(seen_ids),
        )
    }

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        eid = str(row["entity_id"])
        ent = entity_rows.get(eid)
        if not ent:
            continue
        lifecycle = ent.get("lifecycle")
        if lifecycle in ("merged", "deprecated", "reaped"):
            continue
        name = str(ent.get("name") or "")
        source_uri = str(ent.get("source_uri") or "")
        description = str(ent.get("description") or "")
        entity_aliases = _aliases_for_entity(eid, ent, aliases_by_entity)
        identity_match, overlap_score, specificity = _identity_match_and_score(
            tokens,
            entity_id=eid,
            name=name,
            aliases=entity_aliases,
            source_uri=source_uri,
            description=description,
        )
        if overlap_score <= 0:
            continue
        raw_rank = float(row.get("rank") or 0.0)
        weighted = abs(raw_rank) * _type_prior(str(row.get("etype") or ent.get("type") or ""))
        combmax = round(abs(raw_rank) / max_abs_rank, 4)
        hit = {
            "entity_id": eid,
            "entity_name": ent.get("name"),
            "entity_type": ent.get("type"),
            "combmax_score": combmax,
            "retrieval_source": "entity",
            "identity_match": identity_match,
            "identity_specificity": round(specificity, 4),
        }
        scored.append((weighted, combmax, hit))

    scored.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
    return [hit for _weighted, _combmax, hit in scored[:limit]]
