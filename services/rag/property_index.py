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
import shutil
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from universal_event_bus.actor.sequential import SequentialExecutor

from services.rag.fts_index import FtsIndex

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".rag" / "store" / "rag_metadata.db"
_LEGACY_DB_PATH = Path.home() / ".rag" / "store" / "property_index.db"

_V1_BASELINE_SQL = """
CREATE TABLE IF NOT EXISTS properties (
    key TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'all',
    source TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS watermarks (
    step TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_V2_METADATA_SQL = """
CREATE TABLE IF NOT EXISTS corpus_hints (
    scope TEXT NOT NULL,
    term TEXT NOT NULL,
    score REAL NOT NULL,
    prefix TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scope, term)
);
CREATE INDEX IF NOT EXISTS idx_corpus_hints_scope ON corpus_hints(scope);

CREATE TABLE IF NOT EXISTS scope_vocabulary (
    scope TEXT NOT NULL,
    register TEXT NOT NULL,
    term TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scope, register, term)
);
CREATE INDEX IF NOT EXISTS idx_scope_vocabulary_scope ON scope_vocabulary(scope);

CREATE TABLE IF NOT EXISTS articles (
    source_path TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    published_date TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'all',
    content_hash TEXT NOT NULL DEFAULT '',
    subdirectory TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_articles_scope ON articles(scope);
"""

_CREATE_SCHEMA_VERSION_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT NOT NULL
);
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
        self.fts = FtsIndex()

    @property
    def db_path(self) -> Path:
        """Return the active SQLite path for metadata + property index storage."""
        return self._db_path

    def _migration_v1_baseline(self, conn: sqlite3.Connection) -> None:
        """Create baseline schema and backfill columns missing in legacy databases."""
        conn.executescript(_V1_BASELINE_SQL)
        self._ensure_legacy_columns(conn)

    def _ensure_legacy_columns(self, conn: sqlite3.Connection) -> None:
        """Backfill columns from pre-versioned installs before stamping version 1.

        Pre-versioned databases may already have tables created from an older schema.
        CREATE TABLE IF NOT EXISTS will not retrofit those columns, so we ALTER TABLE
        where needed to preserve one authoritative schema state.
        """
        props_cols = {row[1] for row in conn.execute("PRAGMA table_info(properties)")}
        if "scope" not in props_cols:
            conn.execute(
                "ALTER TABLE properties ADD COLUMN scope TEXT NOT NULL DEFAULT 'all'"
            )
        if "source" not in props_cols:
            conn.execute(
                "ALTER TABLE properties ADD COLUMN source TEXT NOT NULL DEFAULT ''"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scope ON properties(scope)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_properties_source ON properties(source)"
        )

        failed_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(failed_extractions)")
        }
        if "attempt_count" not in failed_cols:
            conn.execute(
                "ALTER TABLE failed_extractions "
                "ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 1"
            )
        if "permanent" not in failed_cols:
            conn.execute(
                "ALTER TABLE failed_extractions "
                "ADD COLUMN permanent INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_failed_permanent ON failed_extractions(permanent)"
        )

    def _migration_v2_metadata(self, conn: sqlite3.Connection) -> None:
        """Create normalized metadata tables used by dual-write generators."""
        conn.executescript(_V2_METADATA_SQL)

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply ordered migrations and stamp schema_version rows transactionally."""
        conn.execute(_CREATE_SCHEMA_VERSION_SQL)
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()[0]
        migrations: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
            (1, "baseline tables + indexes + legacy column backfill", self._migration_v1_baseline),
            (
                2,
                "metadata tables: corpus_hints, scope_vocabulary, articles",
                self._migration_v2_metadata,
            ),
        ]
        for version, description, fn in migrations:
            if version <= current:
                continue
            fn(conn)
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
            conn.commit()

    def _migrate_legacy_db_path(self) -> None:
        """Rename legacy property_index DB + sidecars to the metadata DB path."""
        if self._db_path != _DEFAULT_DB_PATH:
            return
        if self._db_path.exists() or not _LEGACY_DB_PATH.exists():
            return
        for suffix in ("", "-wal", "-shm"):
            src = Path(f"{_LEGACY_DB_PATH}{suffix}")
            dst = Path(f"{self._db_path}{suffix}")
            if src.exists():
                shutil.move(str(src), str(dst))
        logger.info("Migrated legacy DB %s -> %s", _LEGACY_DB_PATH, self._db_path)

    async def start(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_db_path()
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            self._apply_migrations(conn)
            await self._seq.start()
        except Exception as e:
            logger.exception("Failed to start PropertyIndex: %s", e)
            conn.close()
            raise
        self._conn = conn
        self.fts.attach(conn, self._seq)
        logger.info("PropertyIndex started: %s", self._db_path)

    async def stop(self) -> None:
        self.fts.detach()
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
        await self.add_batch_with_scope(
            [(k, cid, "all", source) for k, cid in entries]
        )

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
                (chunk_id, source, error, permanent),
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

    async def clear_failures_for_ids(self, source: str, chunk_ids: list[str]) -> None:
        """Remove failure records for the given chunk IDs of a source file.

        Used when a partial write succeeds: clear only the chunks that were
        written so failed chunks retain their attempt count for retry.
        """
        if not chunk_ids:
            return

        async def _write() -> None:
            conn = self._ensure_conn()
            placeholders = ",".join("?" * len(chunk_ids))
            conn.execute(
                "DELETE FROM failed_extractions WHERE source = ? AND chunk_id IN ("
                + placeholders
                + ")",
                (source, *chunk_ids),
            )
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

    async def replace_corpus_hints_rows(
        self, rows: list[tuple[str, str, float, str]]
    ) -> None:
        """Atomically replace all corpus hints rows used for dual-write parity.

        Uses an explicit transaction (BEGIN IMMEDIATE/COMMIT) to avoid a visible
        empty-table window for readers while replacing table contents.
        """

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM corpus_hints")
                if rows:
                    conn.executemany(
                        "INSERT INTO corpus_hints (scope, term, score, prefix)"
                        " VALUES (?, ?, ?, ?)",
                        rows,
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        await self._seq.run(_write())

    async def replace_scope_vocabulary(
        self, vocabulary: dict[str, dict[str, list[str]]]
    ) -> None:
        """Atomically replace scope vocabulary rows from register-structured scope-term payload maps."""
        rows: list[tuple[str, str, str]] = []
        for scope, registers in sorted(vocabulary.items()):
            for register, terms in sorted(registers.items()):
                for term in terms:
                    normalized = term.strip()
                    if normalized:
                        rows.append((scope, register, normalized))

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM scope_vocabulary")
                if rows:
                    conn.executemany(
                        "INSERT INTO scope_vocabulary (scope, register, term)"
                        " VALUES (?, ?, ?)",
                        rows,
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        await self._seq.run(_write())

    async def stamp_watermark(self, step: str) -> None:
        """Record completion of a post-index enrichment step."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT INTO watermarks (step, completed_at)"
                " VALUES (?, datetime('now'))"
                " ON CONFLICT(step) DO UPDATE SET completed_at = datetime('now')",
                (step,),
            )
            conn.commit()

        await self._seq.run(_write())

    def check_watermarks(
        self, required: list[str], reference: str = "reindex"
    ) -> list[str]:
        """Return step names that are stale or missing relative to the reference watermark.

        A step is stale when its completed_at < reference.completed_at, or when
        the step has no watermark entry at all. If the reference itself is missing,
        returns an empty list (no reindex has ever been stamped).
        """
        conn = self._ensure_conn()
        ref_row = conn.execute(
            "SELECT completed_at FROM watermarks WHERE step = ?", (reference,)
        ).fetchone()
        if ref_row is None:
            return []
        ref_ts = ref_row[0]
        if not required:
            return []
        placeholders = ", ".join("?" for _ in required)
        rows = conn.execute(
            f"SELECT step, completed_at FROM watermarks WHERE step IN ({placeholders})",
            required,
        ).fetchall()
        step_to_completed_at = {row[0]: row[1] for row in rows}
        stale: list[str] = []
        for step in required:
            completed_at = step_to_completed_at.get(step)
            if completed_at is None or completed_at < ref_ts:
                stale.append(step)
        return stale

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

    def get_sources(self, prefix: str | None = None) -> list[str]:
        """Return distinct source paths with non-empty source, optionally filtered by prefix."""
        conn = self._ensure_conn()
        if prefix:
            rows = conn.execute(
                "SELECT DISTINCT source FROM properties WHERE source != '' AND source LIKE ? ORDER BY source",
                (f"{prefix}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT source FROM properties WHERE source != '' ORDER BY source"
            ).fetchall()
        return [r[0] for r in rows]

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
        result: defaultdict[str, list[str]] = defaultdict(list)
        for source, chunk_id in rows:
            result[source].append(chunk_id)
        return dict(result)

    def rescope_all(self, scope_resolver: Callable[[str], str]) -> tuple[int, int]:
        """Re-resolve scope for all entries using the given resolver.

        Returns (total_entries, updated_count). Uses a single transaction
        for atomicity.
        """
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT rowid, key, chunk_id, scope, source FROM properties"
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for rowid, key, chunk_id, old_scope, source in rows:
            # Entries with empty source are legacy/unbackfilled rows.
            # They are intentionally skipped to avoid resolver behavior ambiguity.
            if not source:
                continue
            new_scope = scope_resolver(source)
            if new_scope != old_scope:
                updates.append((new_scope, rowid))
        if updates:
            conn.executemany("UPDATE properties SET scope = ? WHERE rowid = ?", updates)
            conn.commit()
        return len(rows), len(updates)


if __name__ == "__main__":
    import sys

    if "--rescope" in sys.argv:
        from services.rag.config import load_config

        config = load_config()
        idx = PropertyIndex()
        import asyncio

        asyncio.run(idx.start())
        try:
            total, updated = idx.rescope_all(config.get_scope_for_path)
            print(f"Rescoped {updated}/{total} property entries")
            conn = idx._ensure_conn()
            for row in conn.execute(
                "SELECT scope, COUNT(*) FROM properties GROUP BY scope ORDER BY scope"
            ).fetchall():
                print(f"  {row[0]}: {row[1]}")
        finally:
            asyncio.run(idx.stop())
    else:
        print("Usage: python -m services.rag.property_index --rescope")
        sys.exit(1)
