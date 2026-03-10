"""SQLite-backed property inverted index for structured RAG.

Maps property keys (prop.name@@stargate, prop.topic@@federation) to chunk IDs.
Enables exact entity/topic lookups that complement vector similarity search.

Concurrency: all writes serialized via SequentialExecutor (no locks).
Reads go directly to SQLite — safe with a single writer.

Key format: prop.{category}@@{value} (case-normalized via .lower()).

Failed extractions are recorded in the ``failed_extractions`` table so callers
can inspect structural failures (e.g. max_tokens exceeded) without tailing logs.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from universal_event_bus.actor.sequential import SequentialExecutor

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".rag" / "store" / "property_index.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS properties (
    key TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    PRIMARY KEY (key, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_key ON properties(key);
CREATE INDEX IF NOT EXISTS idx_chunk ON properties(chunk_id);
CREATE TABLE IF NOT EXISTS pending (
    file TEXT NOT NULL PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS failed_extractions (
    chunk_id TEXT NOT NULL PRIMARY KEY,
    source TEXT NOT NULL,
    error TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    permanent INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_failed_source ON failed_extractions(source);
CREATE INDEX IF NOT EXISTS idx_failed_permanent ON failed_extractions(permanent);
"""


@dataclass(slots=True, kw_only=True)
class FailedChunk:
    chunk_id: str
    source: str
    error: str
    attempt_count: int
    permanent: bool
    recorded_at: str


class PropertyIndex:
    """Inverted index mapping property keys to chunk IDs.

    Write methods route through SequentialExecutor for lock-free serialization.
    Read methods access SQLite directly (safe with single writer).
    """

    def __init__(self, db_path: Path = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._seq = SequentialExecutor()

    def _migrate_add_scope(self, conn: sqlite3.Connection) -> None:
        """Add scope column and index if absent (existing DBs from before this change)."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(properties)")}
        if "scope" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE properties ADD COLUMN scope TEXT NOT NULL DEFAULT 'all'"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_scope ON properties(scope)")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _migrate_add_source(self, conn: sqlite3.Connection) -> None:
        """Add source column (file path) to properties for document frequency scoring."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(properties)")}
        if "source" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE properties ADD COLUMN source TEXT NOT NULL DEFAULT ''"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_properties_source ON properties(source)"
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    async def start(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            conn.executescript(_SCHEMA_SQL)
            # Migrate: add attempt_count if absent (existing DBs from before this change)
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(failed_extractions)")
            }
            if "attempt_count" not in cols:
                conn.execute(
                    "ALTER TABLE failed_extractions"
                    " ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 1"
                )
                conn.commit()
            if "permanent" not in cols:
                conn.execute(
                    "ALTER TABLE failed_extractions"
                    " ADD COLUMN permanent INTEGER NOT NULL DEFAULT 0"
                )
                conn.commit()
            self._migrate_add_scope(conn)
            self._migrate_add_source(conn)
            await self._seq.start()
        except Exception as e:
            logger.exception("Failed to start PropertyIndex: %s", e)
            conn.close()
            raise
        self._conn = conn
        logger.info("PropertyIndex started: %s", self._db_path)

    async def stop(self) -> None:
        await self._seq.stop()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        logger.info("PropertyIndex stopped")

    def _ensure_conn(self) -> sqlite3.Connection:
        assert self._conn is not None, "PropertyIndex not started"
        return self._conn

    # ------------------------------------------------------------------
    # Write methods (serialized through SequentialExecutor)
    # ------------------------------------------------------------------

    async def add(
        self, key: str, chunk_id: str, scope: str = "all", source: str = ""
    ) -> None:
        """Add a property key → chunk_id mapping with optional scope and source."""
        normalized = key.lower()

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT OR IGNORE INTO properties (key, chunk_id, scope, source)"
                " VALUES (?, ?, ?, ?)",
                (normalized, chunk_id, scope, source),
            )
            conn.commit()

        await self._seq.run(_write())

    async def add_batch(self, entries: list[tuple[str, str]], source: str = "") -> None:
        """Add multiple (key, chunk_id) pairs in one transaction. Scope defaults to 'all'."""
        normalized = [(k.lower(), cid, "all", source) for k, cid in entries]

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.executemany(
                "INSERT OR IGNORE INTO properties (key, chunk_id, scope, source)"
                " VALUES (?, ?, ?, ?)",
                normalized,
            )
            conn.commit()

        await self._seq.run(_write())

    async def add_batch_with_scope(
        self, entries: list[tuple[str, str, str, str]]
    ) -> None:
        """Add multiple (key, chunk_id, scope, source) quads in one transaction."""
        normalized = [(k.lower(), cid, scope, src) for k, cid, scope, src in entries]

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.executemany(
                "INSERT OR IGNORE INTO properties (key, chunk_id, scope, source)"
                " VALUES (?, ?, ?, ?)",
                normalized,
            )
            conn.commit()

        await self._seq.run(_write())

    async def remove_chunk(self, chunk_id: str) -> int:
        """Remove all property entries for a chunk. Returns count removed."""

        async def _write() -> int:
            conn = self._ensure_conn()
            cursor = conn.execute(
                "DELETE FROM properties WHERE chunk_id = ?", (chunk_id,)
            )
            conn.commit()
            return cursor.rowcount

        return await self._seq.run(_write())

    async def clear(self) -> None:
        """Remove all entries."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM properties")
            conn.commit()

        await self._seq.run(_write())

    async def mark_pending(self, file: str) -> None:
        """Mark a file as having an in-flight indexing operation."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("INSERT OR IGNORE INTO pending (file) VALUES (?)", (file,))
            conn.commit()

        await self._seq.run(_write())

    async def clear_pending(self, file: str) -> None:
        """Clear the pending mark after successful indexing."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM pending WHERE file = ?", (file,))
            conn.commit()

        await self._seq.run(_write())

    async def record_failure(
        self, chunk_id: str, source: str, error: str, *, permanent: bool = False
    ) -> None:
        """Record an extraction failure; increment attempt_count on repeated failure.

        ∀ chunk_id: attempt_count monotonically increases with each recorded failure.
        permanent=True marks the chunk as permanently abandoned (attempt_count >= max_attempts).
        Once permanent=1, it is never reset to 0.
        """

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT INTO failed_extractions (chunk_id, source, error, attempt_count, permanent)"
                " VALUES (?, ?, ?, 1, ?)"
                " ON CONFLICT(chunk_id) DO UPDATE SET"
                "   error = excluded.error,"
                "   attempt_count = attempt_count + 1,"
                "   permanent = MAX(permanent, excluded.permanent),"
                "   recorded_at = datetime('now')",
                (chunk_id, source, error, int(permanent)),
            )
            conn.commit()

        await self._seq.run(_write())

    async def clear_failures_for(self, source: str) -> None:
        """Remove all failure records for a source file (e.g. after successful recovery)."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM failed_extractions WHERE source = ?", (source,))
            conn.commit()

        await self._seq.run(_write())

    async def backfill_source(self, chunk_to_source: dict[str, str]) -> int:
        """Set source for rows where source is empty. Returns count updated."""
        if not chunk_to_source:
            return 0

        async def _write() -> int:
            conn = self._ensure_conn()
            batch = [(src, cid) for cid, src in chunk_to_source.items() if src]
            cursor = conn.executemany(
                "UPDATE properties SET source = ? WHERE chunk_id = ? AND source = ''",
                batch,
            )
            conn.commit()
            return cursor.rowcount

        return await self._seq.run(_write())

    def get_pending_files(self) -> list[str]:
        """Return all files with in-flight or interrupted indexing.

        Read method — safe to call without SequentialExecutor (single writer).
        Called once at startup before the watcher starts, so no concurrent
        writes are possible.
        """
        conn = self._ensure_conn()
        rows = conn.execute("SELECT file FROM pending").fetchall()
        return [row[0] for row in rows]

    async def rebuild_from_metadata(
        self, metadata_entries: list[tuple[str, str, str]]
    ) -> int:
        """Clear and rebuild from a list of (key, chunk_id, source) triples. Scope set to 'all'."""

        async def _write() -> int:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM properties")
            normalized = [
                (k.lower(), cid, "all", src) for k, cid, src in metadata_entries
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO properties (key, chunk_id, scope, source)"
                " VALUES (?, ?, ?, ?)",
                normalized,
            )
            conn.commit()
            return len(normalized)

        return await self._seq.run(_write())

    # ------------------------------------------------------------------
    # Read methods (direct — safe with single writer)
    # ------------------------------------------------------------------

    def lookup(self, key: str) -> list[str]:
        """Look up chunk IDs by exact property key."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT chunk_id FROM properties WHERE key = ?", (key.lower(),)
        ).fetchall()
        return [row[0] for row in rows]

    def lookup_entity(self, name: str) -> list[str]:
        """Look up chunk IDs by entity name (prop.name@@{name})."""
        return self.lookup(f"prop.name@@{name}")

    def lookup_relations_for(self, entity_name: str) -> list[str]:
        """Look up chunk IDs containing relations where entity_name is subject."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT chunk_id FROM properties WHERE key LIKE ?",
            (f"prop.rel@@{entity_name.lower()}>%",),
        ).fetchall()
        return [row[0] for row in rows]

    def lookup_relations_to(self, entity_name: str) -> list[str]:
        """Look up chunk IDs containing relations where entity_name is target."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT chunk_id FROM properties WHERE key LIKE ?",
            (f"%>{entity_name.lower()}",),
        ).fetchall()
        return [row[0] for row in rows]

    def get_term_counts_by_scope(
        self, key_prefix: str
    ) -> list[tuple[str, str, int, int]]:
        """Return (scope, term, chunk_count, doc_count) for keys matching key_prefix.

        doc_count = COUNT(DISTINCT source) excluding un-backfilled rows (source='').
        Order by chunk_count DESC.
        """
        conn = self._ensure_conn()
        prefix_len = len(key_prefix) + 1  # 1-based substr in SQLite
        like_pattern = f"{key_prefix}%"
        rows = conn.execute(
            "SELECT scope, substr(key, ?),"
            " COUNT(DISTINCT chunk_id),"
            " COUNT(DISTINCT CASE WHEN source != '' THEN source END)"
            " FROM properties WHERE key LIKE ?"
            " GROUP BY scope, substr(key, ?)"
            " ORDER BY COUNT(DISTINCT chunk_id) DESC",
            (prefix_len, like_pattern, prefix_len),
        ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    def get_total_chunks(self) -> int:
        """Return the total number of distinct chunks in the index."""
        conn = self._ensure_conn()
        return conn.execute(
            "SELECT COUNT(DISTINCT chunk_id) FROM properties"
        ).fetchone()[0]

    def get_total_docs(self) -> int:
        """Return the number of distinct source documents with non-empty source."""
        conn = self._ensure_conn()
        return conn.execute(
            "SELECT COUNT(DISTINCT source) FROM properties WHERE source != ''"
        ).fetchone()[0]

    def get_stats(self) -> dict[str, int]:
        """Return property index statistics."""
        conn = self._ensure_conn()
        total = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
        unique_keys = conn.execute(
            "SELECT COUNT(DISTINCT key) FROM properties"
        ).fetchone()[0]
        unique_chunks = conn.execute(
            "SELECT COUNT(DISTINCT chunk_id) FROM properties"
        ).fetchone()[0]
        return {
            "total_entries": total,
            "unique_keys": unique_keys,
            "unique_chunks": unique_chunks,
        }

    def get_failed_chunks(self, source: str | None = None) -> list[FailedChunk]:
        """Return failed extraction records, optionally filtered by source."""
        conn = self._ensure_conn()
        if source is not None:
            rows = conn.execute(
                "SELECT chunk_id, source, error, attempt_count, permanent, recorded_at"
                " FROM failed_extractions WHERE source = ?"
                " ORDER BY recorded_at DESC",
                (source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT chunk_id, source, error, attempt_count, permanent, recorded_at"
                " FROM failed_extractions ORDER BY recorded_at DESC"
            ).fetchall()
        return [
            FailedChunk(
                chunk_id=r[0],
                source=r[1],
                error=r[2],
                attempt_count=r[3],
                permanent=bool(r[4]),
                recorded_at=r[5],
            )
            for r in rows
        ]

    def get_permanent_failures(self, source: str, max_attempts: int) -> set[str]:
        """Return chunk IDs that have exceeded max_attempts for source.

        ∀ chunk_id ∈ result: attempt_count >= max_attempts ∨ permanent = 1.
        """
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT chunk_id FROM failed_extractions"
            " WHERE source = ? AND (attempt_count >= ? OR permanent = 1)",
            (source, max_attempts),
        ).fetchall()
        return {row[0] for row in rows}

    def get_failure_counts(self, source: str) -> dict[str, int]:
        """Return {chunk_id: attempt_count} for source. Diagnostic use."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT chunk_id, attempt_count FROM failed_extractions WHERE source = ?",
            (source,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_failed_count(self) -> int:
        """Return total number of chunks with recorded extraction failures."""
        conn = self._ensure_conn()
        return conn.execute("SELECT COUNT(*) FROM failed_extractions").fetchone()[0]

    def get_permanent_count(self) -> int:
        """Return total number of permanently abandoned chunks."""
        conn = self._ensure_conn()
        return conn.execute(
            "SELECT COUNT(*) FROM failed_extractions WHERE permanent = 1"
        ).fetchone()[0]

    def get_permanent_chunks_by_file(self) -> dict[str, list[str]]:
        """Return {source: [chunk_id, ...]} for all permanently failed chunks."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT source, chunk_id FROM failed_extractions WHERE permanent = 1"
            " ORDER BY source, chunk_id"
        ).fetchall()
        result: dict[str, list[str]] = {}
        for source, chunk_id in rows:
            result.setdefault(source, []).append(chunk_id)
        return result
