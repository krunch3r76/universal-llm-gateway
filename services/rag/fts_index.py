"""SQLite FTS5 full-text index for BM25 sparse retrieval (Pool B backend).

The storage layer for Pool B's vocabulary-aware sparse retrieval. At index time,
every chunk's full text is added to this FTS5 index. At search time, Pool B's
phrase-extracted and IDF-expanded queries are dispatched directly against this
index with ``sparse_only=True`` — bypassing the embedding model entirely.

Within Pool A (the standard dense+sparse hybrid path), this index also provides
the BM25 sidecar: lexical matches merged with dense vector results via mini-RRF
inside the RAG service's ``/search`` endpoint.

BM25 excels at surfacing exact vocabulary matches (PROV-O, Zettelkasten,
SHACL, specific model identifiers like "Q4_K_M") that dense embeddings may
compress away. Pool B's independence from Pool A means these matches don't
have to outcompete twenty fuzzy semantic results in a single ranked list.

Lives in the same SQLite database as the property inverted index so both
stores share one file and one connection. All SQLite access runs on
PropertyIndex's dedicated sqlite thread.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from universal_event_bus.actor.sequential import SequentialExecutor

if TYPE_CHECKING:
    from services.rag.property_index.mixin_01 import _PropertyIndexPart01

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def escape_fts_match_query(query: str) -> str:
    """Escape a user query for FTS5 ``MATCH`` after a colspec parse failure.

    FTS5 treats ``token:term`` as a column filter. Expressions such as
    ``friction:39420`` or ``code: review`` raise ``OperationalError: no such
    column`` when ``token`` is not an indexed column. Double-quoting each
    whitespace-separated token preserves default AND semantics for that fallback.
    """
    stripped = query.strip()
    if not stripped:
        return '""'
    return " ".join(
        f'"{token.replace(chr(34), chr(34) * 2)}"' for token in stripped.split()
    )


def _fetchall_fts_match(
    conn: sqlite3.Connection,
    sql: str,
    query: str,
    extra_params: tuple[object, ...],
) -> list[tuple[object, ...]]:
    """Run an FTS ``MATCH`` query, retrying with ``escape_fts_match_query`` on colspec errors."""
    try:
        return conn.execute(sql, (query, *extra_params)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc).casefold():
            raise
        escaped = escape_fts_match_query(query)
        return conn.execute(sql, (escaped, *extra_params)).fetchall()


_FTS_SCHEMA_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    source UNINDEXED,
    content,
    tokenize='porter unicode61'
);
"""


class FtsIndex:
    """FTS5 full-text index for BM25 sparse retrieval.

    Shares the SQLite connection and SequentialExecutor with PropertyIndex.
    Call ``attach(index, seq)`` after PropertyIndex opens the database.
    """

    def __init__(self) -> None:
        self._index: _PropertyIndexPart01 | None = None
        self._seq: SequentialExecutor | None = None

    def attach(self, index: _PropertyIndexPart01, seq: SequentialExecutor) -> None:
        """Bind to PropertyIndex (sqlite thread + write serializer)."""
        self._index = index
        self._seq = seq
        index.run_sqlite(lambda: index._ensure_conn().executescript(_FTS_SCHEMA_SQL))

    def detach(self) -> None:
        self._index = None
        self._seq = None

    def _ensure(self) -> tuple[_PropertyIndexPart01, SequentialExecutor]:
        assert self._index is not None and self._seq is not None, (
            "FtsIndex not attached"
        )
        return self._index, self._seq

    def _with_conn(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        index, _ = self._ensure()
        return index._with_conn(fn)

    async def _with_conn_async(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        index, _ = self._ensure()
        return await index._with_conn_async(fn)

    # ------------------------------------------------------------------
    # Write methods (serialized through SequentialExecutor)
    # ------------------------------------------------------------------

    async def insert(self, chunk_id: str, source: str, content: str) -> None:
        _, seq = self._ensure()

        async def _write() -> None:
            def _sql(conn: sqlite3.Connection) -> None:
                conn.execute(
                    "INSERT INTO chunks_fts (chunk_id, source, content) VALUES (?, ?, ?)",
                    (chunk_id, source, content),
                )
                conn.commit()

            await self._with_conn_async(_sql)

        await seq.run(_write())

    async def insert_batch(self, entries: list[tuple[str, str, str]]) -> None:
        """Insert (chunk_id, source, content) triples in one transaction."""
        if not entries:
            return
        _, seq = self._ensure()

        async def _write() -> None:
            def _sql(conn: sqlite3.Connection) -> None:
                conn.executemany(
                    "INSERT INTO chunks_fts (chunk_id, source, content) VALUES (?, ?, ?)",
                    entries,
                )
                conn.commit()

            await self._with_conn_async(_sql)

        await seq.run(_write())

    async def remove(self, chunk_id: str) -> None:
        _, seq = self._ensure()

        async def _write() -> None:
            def _sql(conn: sqlite3.Connection) -> None:
                conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
                conn.commit()

            await self._with_conn_async(_sql)

        await seq.run(_write())

    async def remove_batch(self, chunk_ids: list[str]) -> int:
        """Delete FTS entries for the given chunk IDs. Returns rows removed."""
        if not chunk_ids:
            return 0
        _, seq = self._ensure()

        async def _write() -> int:
            def _sql(conn: sqlite3.Connection) -> int:
                cursor = conn.execute(
                    f"DELETE FROM chunks_fts WHERE chunk_id IN ({','.join('?' for _ in chunk_ids)})",
                    chunk_ids,
                )
                conn.commit()
                return cursor.rowcount

            return await self._with_conn_async(_sql)

        return await seq.run(_write())

    async def remove_for_source(self, source: str) -> int:
        """Delete FTS rows stamped with ``source`` (UNINDEXED column)."""
        _, seq = self._ensure()

        async def _write() -> int:
            def _sql(conn: sqlite3.Connection) -> int:
                cursor = conn.execute(
                    "DELETE FROM chunks_fts WHERE source = ?",
                    (source,),
                )
                conn.commit()
                return cursor.rowcount

            return await self._with_conn_async(_sql)

        return await seq.run(_write())

    async def clear(self) -> None:
        _, seq = self._ensure()

        async def _write() -> None:
            def _sql(conn: sqlite3.Connection) -> None:
                conn.execute("DELETE FROM chunks_fts")
                conn.commit()

            await self._with_conn_async(_sql)

        await seq.run(_write())

    # ------------------------------------------------------------------
    # Read methods (sqlite thread)
    # ------------------------------------------------------------------

    def search(self, query: str, *, limit: int = 50) -> list[tuple[str, float]]:
        """BM25 full-text search. Returns (chunk_id, bm25_score) ordered by relevance.

        FTS5 bm25() returns negative floats (more negative = better match).
        """

        def _sql(conn: sqlite3.Connection) -> list[tuple[str, float]]:
            rows = _fetchall_fts_match(
                conn,
                "SELECT chunk_id, bm25(chunks_fts) AS score"
                " FROM chunks_fts WHERE chunks_fts MATCH ?"
                " ORDER BY score LIMIT ?",
                query,
                (limit,),
            )
            return [(row[0], row[1]) for row in rows]

        return self._with_conn(_sql)

    def search_scoped(
        self,
        query: str,
        source_prefixes: list[str],
        *,
        limit: int = 50,
    ) -> list[tuple[str, float]]:
        """BM25 search filtered to sources matching any of the given prefixes."""
        if not source_prefixes:
            return self.search(query, limit=limit)

        def _sql(conn: sqlite3.Connection) -> list[tuple[str, float]]:
            source_clause = " OR ".join("source LIKE ?" for _ in source_prefixes)
            prefix_params = tuple(f"{prefix}%" for prefix in source_prefixes)
            rows = _fetchall_fts_match(
                conn,
                "SELECT chunk_id, source, bm25(chunks_fts) AS score"
                f" FROM chunks_fts WHERE chunks_fts MATCH ? AND ({source_clause})"
                " ORDER BY score LIMIT ?",
                query,
                (*prefix_params, limit),
            )
            return [(row[0], row[2]) for row in rows]

        return self._with_conn(_sql)

    def sources_for_prefixes(self, source_prefixes: list[str]) -> list[str]:
        """Distinct indexed paths under any prefix, for Chroma ``source`` ``$in``.

        Same ``LIKE prefix%`` predicate as ``search_scoped``. Empty prefixes
        return no paths. Callers use the list as a metadata prefilter so ANN
        runs inside the scope instead of on the global neighbor list.
        """
        if not source_prefixes:
            return []

        def _sql(conn: sqlite3.Connection) -> list[str]:
            source_clause = " OR ".join("source LIKE ?" for _ in source_prefixes)
            params = tuple(f"{prefix}%" for prefix in source_prefixes)
            rows = conn.execute(
                f"SELECT DISTINCT source FROM chunks_fts WHERE {source_clause}",
                params,
            ).fetchall()
            return [row[0] for row in rows]

        return self._with_conn(_sql)

    def get_count(self) -> int:
        """Return total number of rows in the FTS5 table."""

        def _sql(conn: sqlite3.Connection) -> int:
            return conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

        return self._with_conn(_sql)
