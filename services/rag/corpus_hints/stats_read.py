"""Corpus-hint statistics read on a private SQLite connection.

``update_corpus_hints`` runs after every successful file index. It used to
scan ``properties`` once per configured scope on the RAG asyncio thread
(about 79 scopes, two key prefixes, ~2e6 rows). That scan does not await, so
the process serves nothing else for the duration — about 60s on the live
index. ``GET /scopes`` then misses its 5s client timeout and Stargate's
passthrough returns 503.

Reads here open a read-only connection and aggregate once per key prefix,
inside one read transaction so the counts are a single snapshot. When
``only_scope`` is set, the term scan is restricted to that scope's source
prefixes instead of the whole corpus. Scope membership uses the same
ASCII-nocase prefix match as SQLite ``LIKE prefix || '%'`` (``%`` and ``_``
inside a prefix stay wildcards; ``%`` also matches newlines). Callers run
this function via ``asyncio.to_thread`` so the event loop can keep serving
``/scopes``.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_BUSY_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class CorpusHintStats:
    """Term and document counts the hint scorer consumes.

    ``scope_prefix_terms`` maps scope → key prefix → (term, chunk_count,
    doc_count). ``scope_doc_counts`` is distinct source paths under that
    scope's prefixes, including sources that have no name or topic keys.
    Both maps are empty when ``total_chunks`` is 0. When statistics come
    from the ``scope`` column rather than source prefixes,
    ``scope_doc_counts`` stays empty and the scorer keeps its default
    minimum document count.
    """

    total_chunks: int
    total_docs: int
    scope_prefix_terms: dict[str, dict[str, list[tuple[str, int, int]]]] = field(
        default_factory=dict
    )
    scope_doc_counts: dict[str, int] = field(default_factory=dict)


def read_corpus_hint_stats(
    db_path: Path,
    *,
    configured_scopes: dict[str, list[str]] | None,
    key_prefixes: list[str],
    only_scope: str | None,
) -> CorpusHintStats:
    """Return hint inputs for ``db_path`` without using the service connection.

    ``configured_scopes`` selects the source-prefix aggregation used after
    indexing. ``None`` groups by the ``properties.scope`` column instead,
    which is the fallback when the caller has no scope config.
    """
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        # One read transaction: WAL gives a stable snapshot across the statements.
        conn.execute("BEGIN")
        total_chunks = int(
            conn.execute(
                "SELECT COUNT(DISTINCT chunk_id) FROM properties"
            ).fetchone()[0]
        )
        if total_chunks == 0:
            return CorpusHintStats(total_chunks=0, total_docs=0)
        total_docs = int(
            conn.execute(
                "SELECT COUNT(DISTINCT source) FROM properties WHERE source != ''"
            ).fetchone()[0]
        )
        if configured_scopes is None:
            terms = _terms_by_scope_column(conn, key_prefixes, only_scope)
            return CorpusHintStats(
                total_chunks=total_chunks,
                total_docs=total_docs,
                scope_prefix_terms=terms,
            )
        terms, doc_counts = _terms_by_source_prefix(
            conn,
            configured_scopes,
            key_prefixes,
            only_scope,
        )
        return CorpusHintStats(
            total_chunks=total_chunks,
            total_docs=total_docs,
            scope_prefix_terms=terms,
            scope_doc_counts=doc_counts,
        )
    finally:
        conn.rollback()
        conn.close()


def _terms_by_scope_column(
    conn: sqlite3.Connection,
    key_prefixes: list[str],
    only_scope: str | None,
) -> dict[str, dict[str, list[tuple[str, int, int]]]]:
    terms: dict[str, dict[str, list[tuple[str, int, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for key_prefix in key_prefixes:
        prefix_len = len(key_prefix) + 1
        scope_sql = ""
        scope_params: tuple[str, ...] = ()
        if only_scope is not None:
            scope_sql = " AND scope = ?"
            scope_params = (only_scope,)
        rows = conn.execute(
            "SELECT scope, substr(key, ?),"
            " COUNT(DISTINCT chunk_id),"
            " COUNT(DISTINCT CASE WHEN source != '' THEN source END)"
            " FROM properties WHERE key LIKE ?"
            f"{scope_sql}"
            " GROUP BY scope, substr(key, ?)",
            (prefix_len, f"{key_prefix}%", *scope_params, prefix_len),
        )
        for scope_name, term, chunk_count, doc_count in rows:
            if only_scope is not None and scope_name != only_scope:
                continue
            if not term:
                continue
            terms[scope_name][key_prefix].append(
                (str(term), int(chunk_count), int(doc_count))
            )
    return {scope: dict(prefix_map) for scope, prefix_map in terms.items()}


def _terms_by_source_prefix(
    conn: sqlite3.Connection,
    configured_scopes: dict[str, list[str]],
    key_prefixes: list[str],
    only_scope: str | None,
) -> tuple[
    dict[str, dict[str, list[tuple[str, int, int]]]],
    dict[str, int],
]:
    source_limit: list[str] | None = None
    if only_scope is not None:
        source_limit = list(configured_scopes.get(only_scope) or [])
    sources = _distinct_sources(conn, source_limit)
    folded_sources = [(_ascii_lower(source), source) for source in sources]
    per_prefix: dict[str, dict[str, dict[str, int]]] = {}
    for key_prefix in key_prefixes:
        per_prefix[key_prefix] = _chunk_counts_by_source(
            conn, key_prefix, source_limit
        )

    scope_terms: dict[str, dict[str, list[tuple[str, int, int]]]] = {}
    scope_doc_counts: dict[str, int] = {}
    for scope_name, source_prefixes in configured_scopes.items():
        if only_scope is not None and scope_name != only_scope:
            continue
        if not source_prefixes:
            scope_doc_counts[scope_name] = 0
            continue
        matches = _matcher(source_prefixes)
        matched = {
            source for folded, source in folded_sources if matches(folded)
        }
        scope_doc_counts[scope_name] = len(matched)
        prefix_terms: dict[str, list[tuple[str, int, int]]] = {}
        for key_prefix in key_prefixes:
            prefix_terms[key_prefix] = _sum_terms(
                per_prefix[key_prefix], matched
            )
        scope_terms[scope_name] = prefix_terms
    return scope_terms, scope_doc_counts


def _like_clause(prefixes: list[str]) -> tuple[str, tuple[str, ...]]:
    """SQL fragment matching SQLite ``source LIKE prefix || '%'`` for each prefix."""
    clause = " OR ".join("source LIKE ?" for _ in prefixes)
    params = tuple(f"{prefix}%" for prefix in prefixes)
    return f"({clause})", params


def _distinct_sources(
    conn: sqlite3.Connection, source_prefixes: list[str] | None
) -> list[str]:
    if source_prefixes is not None and not source_prefixes:
        return []
    sql = "SELECT DISTINCT source FROM properties WHERE source != ''"
    params: tuple[str, ...] = ()
    if source_prefixes is not None:
        clause, params = _like_clause(source_prefixes)
        sql = f"{sql} AND {clause}"
    return [str(row[0]) for row in conn.execute(sql, params)]


def _chunk_counts_by_source(
    conn: sqlite3.Connection,
    key_prefix: str,
    source_prefixes: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Map source → term → distinct chunk count for one key prefix.

    A ``(key, chunk_id)`` row has one source, so summing these per-source
    counts equals ``COUNT(DISTINCT chunk_id)`` across sources.
    """
    if source_prefixes is not None and not source_prefixes:
        return {}
    prefix_len = len(key_prefix) + 1
    sql = (
        "SELECT source, substr(key, ?), COUNT(DISTINCT chunk_id)"
        " FROM properties"
        " WHERE source != '' AND key LIKE ?"
    )
    params: tuple[object, ...] = (prefix_len, f"{key_prefix}%")
    if source_prefixes is not None:
        clause, like_params = _like_clause(source_prefixes)
        sql = f"{sql} AND {clause}"
        params = (*params, *like_params)
    sql = f"{sql} GROUP BY source, substr(key, ?)"
    params = (*params, prefix_len)
    rows = conn.execute(sql, params)
    by_source: dict[str, dict[str, int]] = defaultdict(dict)
    for source, term, chunk_count in rows:
        if not term:
            continue
        by_source[str(source)][str(term)] = int(chunk_count)
    return by_source


def _sum_terms(
    by_source: dict[str, dict[str, int]],
    matched_sources: set[str],
) -> list[tuple[str, int, int]]:
    chunk_sums: dict[str, int] = defaultdict(int)
    doc_counts: dict[str, int] = defaultdict(int)
    for source in matched_sources:
        for term, chunk_count in by_source.get(source, {}).items():
            chunk_sums[term] += chunk_count
            doc_counts[term] += 1
    return [
        (term, chunk_sums[term], doc_counts[term]) for term in chunk_sums
    ]


def _matcher(prefixes: list[str]) -> Callable[[str], bool]:
    """Return a predicate on an already ASCII-lowered source path."""
    literals: list[str] = []
    patterns: list[re.Pattern[str]] = []
    for prefix in prefixes:
        folded = _ascii_lower(prefix)
        if "%" in folded or "_" in folded:
            body = "".join(
                ".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
                for ch in folded
            )
            # DOTALL: SQLite LIKE '%' matches newlines; '.' does not.
            patterns.append(re.compile(f"^{body}.*$", re.DOTALL))
        else:
            literals.append(folded)

    def matches(source_folded: str) -> bool:
        if any(source_folded.startswith(prefix) for prefix in literals):
            return True
        return any(pattern.match(source_folded) is not None for pattern in patterns)

    return matches


def _ascii_lower(text: str) -> str:
    """Fold A–Z only, matching SQLite's default ``LIKE`` nocase rules."""
    return "".join(ch.lower() if "A" <= ch <= "Z" else ch for ch in text)
