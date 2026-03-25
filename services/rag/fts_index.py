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
stores share one file and one connection. Concurrency model matches
PropertyIndex: writes serialized via SequentialExecutor, reads go directly
to SQLite.
"""

from __future__ import annotations

import logging
import sqlite3

from universal_event_bus.actor.sequential import SequentialExecutor

logger = logging.getLogger(__name__)

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
    Call ``attach(conn, seq)`` after PropertyIndex opens the database.
    """

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None
        self._seq: SequentialExecutor | None = None

    def attach(self, conn: sqlite3.Connection, seq: SequentialExecutor) -> None:
        """Bind to an existing connection and executor (called by PropertyIndex.start)."""
        self._conn = conn
        self._seq = seq
        conn.executescript(_FTS_SCHEMA_SQL)

    def detach(self) -> None:
        self._conn = None
        self._seq = None

    def _ensure(self) -> tuple[sqlite3.Connection, SequentialExecutor]:
        assert self._conn is not None and self._seq is not None, "FtsIndex not attached"
        return self._conn, self._seq

    # ------------------------------------------------------------------
    # Write methods (serialized through SequentialExecutor)
    # ------------------------------------------------------------------

    async def insert(self, chunk_id: str, source: str, content: str) -> None:
        conn, seq = self._ensure()

        async def _write() -> None:
            conn.execute(
                "INSERT INTO chunks_fts (chunk_id, source, content) VALUES (?, ?, ?)",
                (chunk_id, source, content),
            )
            conn.commit()

        await seq.run(_write())

    async def insert_batch(self, entries: list[tuple[str, str, str]]) -> None:
        """Insert (chunk_id, source, content) triples in one transaction."""
        if not entries:
            return
        conn, seq = self._ensure()

        async def _write() -> None:
            conn.executemany(
                "INSERT INTO chunks_fts (chunk_id, source, content) VALUES (?, ?, ?)",
                entries,
            )
            conn.commit()

        await seq.run(_write())

    async def remove(self, chunk_id: str) -> None:
        conn, seq = self._ensure()

        async def _write() -> None:
            conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
            conn.commit()

        await seq.run(_write())

    async def remove_batch(self, chunk_ids: list[str]) -> int:
        """Delete FTS entries for the given chunk IDs. Returns rows removed."""
        if not chunk_ids:
            return 0
        conn, seq = self._ensure()

        async def _write() -> int:
            cursor = conn.execute(
                f"DELETE FROM chunks_fts WHERE chunk_id IN ({','.join('?' for _ in chunk_ids)})",
                chunk_ids,
            )
            conn.commit()
            return cursor.rowcount

        return await seq.run(_write())

    async def clear(self) -> None:
        conn, seq = self._ensure()

        async def _write() -> None:
            conn.execute("DELETE FROM chunks_fts")
            conn.commit()

        await seq.run(_write())

    # ------------------------------------------------------------------
    # Read methods (direct — safe with single writer)
    # ------------------------------------------------------------------

    def search(self, query: str, *, limit: int = 50) -> list[tuple[str, float]]:
        """BM25 full-text search. Returns (chunk_id, bm25_score) ordered by relevance.

        FTS5 bm25() returns negative floats (more negative = better match).
        """
        conn, _ = self._ensure()
        rows = conn.execute(
            "SELECT chunk_id, bm25(chunks_fts) AS score"
            " FROM chunks_fts WHERE chunks_fts MATCH ?"
            " ORDER BY score LIMIT ?",
            (query, limit),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def search_scoped(
        self,
        query: str,
        source_prefixes: list[str],
        *,
        limit: int = 50,
    ) -> list[tuple[str, float]]:
        """BM25 search filtered to sources matching any of the given prefixes."""
        conn, _ = self._ensure()
        if not source_prefixes:
            return self.search(query, limit=limit)
        # FTS5 MATCH on content, then Python-side prefix filter.
        # At 8K docs this is efficient; FTS5 narrows the candidate set first.
        rows = conn.execute(
            "SELECT chunk_id, source, bm25(chunks_fts) AS score"
            " FROM chunks_fts WHERE chunks_fts MATCH ?"
            " ORDER BY score LIMIT ?",
            (query, limit * 3),
        ).fetchall()
        filtered: list[tuple[str, float]] = []
        for chunk_id, source, score in rows:
            if any(source.startswith(p) for p in source_prefixes):
                filtered.append((chunk_id, score))
                if len(filtered) >= limit:
                    break
        return filtered

    def get_count(self) -> int:
        """Return total number of rows in the FTS5 table."""
        conn, _ = self._ensure()
        return conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
