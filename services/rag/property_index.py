"""SQLite-backed property inverted index for structured RAG.

Maps property keys (prop.name@@stargate, prop.topic@@federation) to chunk IDs.
Enables exact entity/topic lookups that complement vector similarity search.

Concurrency: all writes serialized via SequentialExecutor (no locks).
Reads go directly to SQLite — safe with a single writer.

Key format: prop.{category}@@{value} (case-normalized via .lower()).
"""

from __future__ import annotations

import logging
import sqlite3
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
"""


class PropertyIndex:
    """Inverted index mapping property keys to chunk IDs.

    Write methods route through SequentialExecutor for lock-free serialization.
    Read methods access SQLite directly (safe with single writer).
    """

    def __init__(self, db_path: Path = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._seq = SequentialExecutor()

    async def start(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            conn.executescript(_SCHEMA_SQL)
            await self._seq.start()
        except Exception:
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

    async def add(self, key: str, chunk_id: str) -> None:
        """Add a property key → chunk_id mapping."""
        normalized = key.lower()

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT OR IGNORE INTO properties (key, chunk_id) VALUES (?, ?)",
                (normalized, chunk_id),
            )
            conn.commit()

        await self._seq.run(_write())

    async def add_batch(self, entries: list[tuple[str, str]]) -> None:
        """Add multiple (key, chunk_id) pairs in one transaction."""
        normalized = [(k.lower(), cid) for k, cid in entries]

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.executemany(
                "INSERT OR IGNORE INTO properties (key, chunk_id) VALUES (?, ?)",
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
            conn.execute(
                "INSERT OR IGNORE INTO pending (file) VALUES (?)", (file,)
            )
            conn.commit()

        await self._seq.run(_write())

    async def clear_pending(self, file: str) -> None:
        """Clear the pending mark after successful indexing."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM pending WHERE file = ?", (file,))
            conn.commit()

        await self._seq.run(_write())

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
        self, metadata_entries: list[tuple[str, str]]
    ) -> int:
        """Clear and rebuild from a list of (key, chunk_id) pairs.

        Returns the number of entries inserted.
        """

        async def _write() -> int:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM properties")
            normalized = [(k.lower(), cid) for k, cid in metadata_entries]
            conn.executemany(
                "INSERT OR IGNORE INTO properties (key, chunk_id) VALUES (?, ?)",
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
