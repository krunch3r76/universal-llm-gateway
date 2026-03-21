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
from dataclasses import dataclass
from pathlib import Path

from universal_event_bus.actor.sequential import SequentialExecutor

from services.rag.article_registry import ArticleEntry
from services.rag.fts_index import FtsIndex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".rag" / "store" / "rag_metadata.db"
_LEGACY_DB_PATH = Path.home() / ".rag" / "store" / "property_index.db"


def _row_str(value: object) -> str:
    return "" if value is None else str(value)


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
    comments TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_articles_scope ON articles(scope);
"""

_V4_SOURCE_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS indexed_sources (
    source TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    extraction_schema_version INTEGER NOT NULL,
    extraction_model TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_indexed_sources_updated_at ON indexed_sources(updated_at);
"""

_V5_SCOPE_FRESHNESS_SQL = """
CREATE TABLE IF NOT EXISTS scope_freshness (
    scope TEXT PRIMARY KEY,
    files_hash TEXT NOT NULL,
    classified_at TEXT NOT NULL DEFAULT (datetime('now'))
);
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


@dataclass(slots=True, kw_only=True)
class PendingSnapshot:
    """Bounded view of pending journal rows for operational status APIs."""

    count: int
    sample: list[str]


@dataclass(slots=True, kw_only=True)
class FailureSnapshot:
    """Count view of failed extraction rows used by status/health endpoints."""

    failed_extractions_count: int
    failed_extractions_permanent_count: int


@dataclass(slots=True, kw_only=True)
class IndexedSourceSnapshot:
    """Cached source freshness row used for stat-first unchanged checks."""

    source: str
    mtime_ns: int
    size_bytes: int
    extraction_schema_version: int
    extraction_model: str
    updated_at: str


class PropertyIndex:
    """SQLite-backed inverted index mapping property keys to chunk IDs.

    Property keys use ``prop.{category}@@{value}`` (for example:
    ``prop.name@@stargate``) so entity/topic filters can do exact lookup
    against chunk IDs and complement vector retrieval.

    Write methods route through SequentialExecutor for lock-free serialization.
    Read methods access SQLite directly (safe with single writer).
    """

    def __init__(self, db_path: Path = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._seq = SequentialExecutor()
        self.fts: FtsIndex = FtsIndex()

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

        def _migration_v3_articles_comments(conn: sqlite3.Connection) -> None:
            """Adds the 'comments' column to the 'articles' table.

            This migration ensures that the 'articles' table schema is aligned with
            the ArticleEntry dataclass, allowing for storage of additional metadata
            or user-provided comments associated with an article. This column was
            missing in previous schema versions.
            """
            cols = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
            if "comments" not in cols:
                conn.execute(
                    "ALTER TABLE articles ADD COLUMN comments TEXT NOT NULL DEFAULT ''"
                )

        def _migration_v4_indexed_sources(conn: sqlite3.Connection) -> None:
            """Creates the 'indexed_sources' table.

            This table acts as a cache for source file metadata (mtime, size, schema version)
            to enable 'stat-first' checks, significantly speeding up re-indexing by avoiding
            costly content hash computations for unchanged files.
            """
            conn.executescript(_V4_SOURCE_CACHE_SQL)

        def _migration_v5_scope_freshness(conn: sqlite3.Connection) -> None:
            """Creates the 'scope_freshness' table.

            This table stores a hash of filenames per scope, allowing the system to
            quickly determine if the set of files within a scope has changed. This is
            crucial for automatically triggering corpus hint or vocabulary repair
            processes when staleness is detected.
            """
            conn.executescript(_V5_SCOPE_FRESHNESS_SQL)

        def _migration_v6_classified_tier(conn: sqlite3.Connection) -> None:
            """Adds the 'classified_tier' column to the 'scope_freshness' table.

            This column records the processing tier (e.g., 'local' or 'frontier')
            that last classified the vocabulary for a given scope. This helps in
            optimizing vocabulary pipeline runs by allowing skips for scopes that
            are already fresh according to the current pipeline mode.
            """
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(scope_freshness)")
            }
            if "classified_tier" not in cols:
                conn.execute(
                    "ALTER TABLE scope_freshness ADD COLUMN classified_tier "
                    "TEXT NOT NULL DEFAULT 'local'"
                )

        migrations: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
            (
                1,
                "baseline tables + indexes + legacy column backfill",
                self._migration_v1_baseline,
            ),
            (
                2,
                "metadata tables: corpus_hints, scope_vocabulary, articles",
                self._migration_v2_metadata,
            ),
            (
                3,
                "articles.comments column for ArticleEntry parity",
                _migration_v3_articles_comments,
            ),
            (
                4,
                "source freshness cache for mtime-first indexing checks",
                _migration_v4_indexed_sources,
            ),
            (
                5,
                "scope_freshness: file-list hash per scope for hint/vocab staleness",
                _migration_v5_scope_freshness,
            ),
            (
                6,
                "scope_freshness.classified_tier for vocabulary pipeline skip-fresh",
                _migration_v6_classified_tier,
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
        await self.add_batch_with_scope([(k, cid, "all", source) for k, cid in entries])

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

    async def remove_properties_for_chunks(self, chunk_ids: list[str]) -> int:
        """Remove property entries for a batch of chunk IDs. Returns total removed."""

        async def _write() -> int:
            conn = self._ensure_conn()
            if not chunk_ids:
                return 0
            placeholders = ",".join("?" for _ in chunk_ids)
            cursor = conn.execute(
                f"DELETE FROM properties WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            conn.commit()
            return cursor.rowcount

        return await self._seq.run(_write())

    async def clear(self) -> None:
        """Remove all entries."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM properties")
            conn.execute("DELETE FROM indexed_sources")
            conn.commit()

        await self._seq.run(_write())
        await self.fts.clear()

    async def upsert_indexed_source(
        self,
        *,
        source: str,
        mtime_ns: int,
        size_bytes: int,
        extraction_schema_version: int,
        extraction_model: str,
    ) -> None:
        """Record the latest successfully evaluated source state for fast unchanged checks."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT INTO indexed_sources ("
                "  source, mtime_ns, size_bytes, extraction_schema_version, extraction_model, updated_at"
                ") VALUES (?, ?, ?, ?, ?, datetime('now'))"
                " ON CONFLICT(source) DO UPDATE SET"
                "  mtime_ns = excluded.mtime_ns,"
                "  size_bytes = excluded.size_bytes,"
                "  extraction_schema_version = excluded.extraction_schema_version,"
                "  extraction_model = excluded.extraction_model,"
                "  updated_at = datetime('now')",
                (
                    source,
                    mtime_ns,
                    size_bytes,
                    extraction_schema_version,
                    extraction_model,
                ),
            )
            conn.commit()

        await self._seq.run(_write())

    async def remove_indexed_source(self, source: str) -> None:
        """Remove one cached source freshness row."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM indexed_sources WHERE source = ?", (source,))
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

    async def upsert_article(
        self,
        source_path: str,
        filename: str,
        *,
        title: str = "",
        authors: str = "",
        venue: str = "",
        published_date: str = "",
        doi: str = "",
        abstract: str = "",
        content_hash: str = "",
        subdirectory: str = "",
        scope: str = "all",
    ) -> bool:
        """Insert or update an articles row. Returns True if a new row was created."""

        async def _write() -> bool:
            conn = self._ensure_conn()
            existing = conn.execute(
                "SELECT 1 FROM articles WHERE source_path = ?", (source_path,)
            ).fetchone()
            conn.execute(
                "INSERT INTO articles ("
                "  source_path, filename, title, authors, venue, published_date,"
                "  doi, abstract, scope, content_hash, subdirectory, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))"
                " ON CONFLICT(source_path) DO UPDATE SET"
                "  filename = excluded.filename,"
                "  title = CASE WHEN excluded.title != '' THEN excluded.title ELSE articles.title END,"
                "  authors = CASE WHEN excluded.authors != '' THEN excluded.authors ELSE articles.authors END,"
                "  venue = CASE WHEN excluded.venue != '' THEN excluded.venue ELSE articles.venue END,"
                "  published_date = CASE WHEN excluded.published_date != '' THEN excluded.published_date ELSE articles.published_date END,"
                "  doi = CASE WHEN excluded.doi != '' THEN excluded.doi ELSE articles.doi END,"
                "  abstract = CASE WHEN excluded.abstract != '' THEN excluded.abstract ELSE articles.abstract END,"
                "  scope = excluded.scope,"
                "  content_hash = CASE WHEN excluded.content_hash != '' THEN excluded.content_hash ELSE articles.content_hash END,"
                "  subdirectory = CASE WHEN excluded.subdirectory != '' THEN excluded.subdirectory ELSE articles.subdirectory END,"
                "  updated_at = datetime('now')",
                (
                    source_path,
                    filename,
                    title,
                    authors,
                    venue,
                    published_date,
                    doi,
                    abstract,
                    scope,
                    content_hash,
                    subdirectory,
                ),
            )
            conn.commit()
            return existing is None

        return await self._seq.run(_write())

    def article_exists(self, source_path: str) -> bool:
        """Return whether an articles row already exists for the exact source path."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT 1 FROM articles WHERE source_path = ?",
            (source_path,),
        ).fetchone()
        return row is not None

    async def sync_article_structural_fields(
        self,
        *,
        source_path: str,
        filename: str,
        content_hash: str,
        scope: str,
        subdirectory: str,
    ) -> bool:
        """Create or refresh non-curated article identity fields.

        Returns True when a new row was created. Existing rows keep curated fields
        like title/authors/venue while structural fields stay aligned with the
        latest indexed file state.
        """

        async def _write() -> bool:
            conn = self._ensure_conn()
            existing = conn.execute(
                "SELECT 1 FROM articles WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            conn.execute(
                "INSERT INTO articles ("
                "  source_path, filename, scope, content_hash, subdirectory, updated_at"
                ") VALUES (?, ?, ?, ?, ?, datetime('now'))"
                " ON CONFLICT(source_path) DO UPDATE SET"
                "  filename = excluded.filename,"
                "  scope = excluded.scope,"
                "  content_hash = excluded.content_hash,"
                "  subdirectory = excluded.subdirectory,"
                "  updated_at = datetime('now')",
                (source_path, filename, scope, content_hash, subdirectory),
            )
            conn.commit()
            return existing is None

        return await self._seq.run(_write())

    async def remove_article(self, source_path: str) -> bool:
        """Remove the articles row for a source path. Returns True if a row existed."""

        async def _write() -> bool:
            conn = self._ensure_conn()
            cursor = conn.execute(
                "DELETE FROM articles WHERE source_path = ?", (source_path,)
            )
            conn.commit()
            return cursor.rowcount > 0

        return await self._seq.run(_write())

    async def remove_articles_by_prefix(self, prefix: str) -> int:
        """Remove all articles rows whose source_path starts with *prefix*."""

        async def _write() -> int:
            conn = self._ensure_conn()
            cursor = conn.execute(
                "DELETE FROM articles WHERE source_path LIKE ? ESCAPE '\\'",
                (prefix.replace("%", "\\%").replace("_", "\\_") + "%",),
            )
            conn.commit()
            return cursor.rowcount

        return await self._seq.run(_write())

    async def remove_source_metadata(
        self,
        source: str,
        chunk_ids: list[str] | None = None,
        *,
        remove_article: bool = True,
    ) -> None:
        """Remove source-scoped SQLite metadata for one source.

        Watcher cleanup may preserve the article row so a later move-detection or
        re-index path can reuse curated metadata. Admin delete paths keep the
        default `remove_article=True` and remain fully destructive.
        """
        normalized_chunk_ids = list(dict.fromkeys(chunk_ids or []))

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM failed_extractions WHERE source = ?", (source,))
            if remove_article:
                conn.execute("DELETE FROM articles WHERE source_path = ?", (source,))
            conn.execute("DELETE FROM indexed_sources WHERE source = ?", (source,))
            if normalized_chunk_ids:
                placeholders = ",".join("?" for _ in normalized_chunk_ids)
                conn.execute(
                    f"DELETE FROM properties WHERE chunk_id IN ({placeholders})",
                    normalized_chunk_ids,
                )
            conn.commit()

        await self._seq.run(_write())
        if normalized_chunk_ids:
            await self.fts.remove_batch(normalized_chunk_ids)

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

        async def _write() -> None:
            conn = self._ensure_conn()
            if not chunk_ids:
                return
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

        async def _write() -> int:
            conn = self._ensure_conn()
            if not chunk_to_source:
                return 0
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
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception("replace_corpus_hints_rows failed: %s", e)
                raise

        await self._seq.run(_write())

    async def replace_corpus_hints_for_scope(
        self, scope: str, rows: list[tuple[str, str, float, str]]
    ) -> None:
        """Atomically replace corpus hints for a single scope.

        Deletes only the rows matching *scope*, then inserts the new rows.
        Other scopes' hints remain untouched.
        """

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM corpus_hints WHERE scope = ?", (scope,))
                if rows:
                    conn.executemany(
                        "INSERT INTO corpus_hints (scope, term, score, prefix)"
                        " VALUES (?, ?, ?, ?)",
                        rows,
                    )
                conn.execute("COMMIT")
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception(
                    "replace_corpus_hints_for_scope(%s) failed: %s", scope, e
                )
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
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception("replace_scope_vocabulary failed: %s", e)
                raise

        await self._seq.run(_write())

    async def replace_scope_vocabulary_for_scopes(
        self, vocabulary: dict[str, dict[str, list[str]]]
    ) -> None:
        """Replace vocabulary rows only for scopes present in *vocabulary*.

        Other scopes' rows are left unchanged (unlike replace_scope_vocabulary).
        """
        if not vocabulary:
            return
        rows: list[tuple[str, str, str]] = []
        scope_names: list[str] = []
        for scope, registers in sorted(vocabulary.items()):
            scope_names.append(scope)
            for register, terms in sorted(registers.items()):
                for term in terms:
                    normalized = term.strip()
                    if normalized:
                        rows.append((scope, register, normalized))

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                placeholders = ",".join("?" for _ in scope_names)
                conn.execute(
                    f"DELETE FROM scope_vocabulary WHERE scope IN ({placeholders})",
                    scope_names,
                )
                if rows:
                    conn.executemany(
                        "INSERT INTO scope_vocabulary (scope, register, term)"
                        " VALUES (?, ?, ?)",
                        rows,
                    )
                conn.execute("COMMIT")
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception("replace_scope_vocabulary_for_scopes failed: %s", e)
                raise

        await self._seq.run(_write())

    def get_scope_freshness(self, scope: str) -> tuple[str, str, str] | None:
        """Return (files_hash, classified_at, classified_tier) if a row exists."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT files_hash, classified_at, classified_tier FROM scope_freshness "
            "WHERE scope = ?",
            (scope,),
        ).fetchone()
        if row is None:
            return None
        tier_raw = row[2] if len(row) > 2 else "local"
        tier = str(tier_raw) if tier_raw is not None else "local"
        return (str(row[0]), str(row[1]), tier)

    async def store_scope_freshness(
        self,
        scope: str,
        files_hash: str,
        *,
        classified_tier: str = "local",
    ) -> None:
        """Persist per-scope file-list hash and classification tier after repair."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT INTO scope_freshness (scope, files_hash, classified_at, "
                "classified_tier)"
                " VALUES (?, ?, datetime('now'), ?)"
                " ON CONFLICT(scope) DO UPDATE SET"
                " files_hash = excluded.files_hash,"
                " classified_at = datetime('now'),"
                " classified_tier = excluded.classified_tier",
                (scope, files_hash, classified_tier),
            )
            conn.commit()

        await self._seq.run(_write())

    async def invalidate_scope_freshness(self, scope: str) -> None:
        """Delete scope_freshness row so next run reclassifies this scope."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "DELETE FROM scope_freshness WHERE scope = ?", (scope,)
            )
            conn.commit()

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

    def get_indexed_source(self, source: str) -> IndexedSourceSnapshot | None:
        """Return the cached source freshness row, if present."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT source, mtime_ns, size_bytes, extraction_schema_version,"
            " extraction_model, updated_at"
            " FROM indexed_sources WHERE source = ?",
            (source,),
        ).fetchone()
        if row is None:
            return None
        return IndexedSourceSnapshot(
            source=row[0],
            mtime_ns=row[1],
            size_bytes=row[2],
            extraction_schema_version=row[3],
            extraction_model=row[4],
            updated_at=row[5],
        )

    def get_pending_snapshot(self, sample_limit: int = 20) -> PendingSnapshot:
        """Return pending journal count plus a bounded file sample.

        Used by operational status APIs that need O(1)+O(limit) visibility
        without loading the full pending table into memory.
        """
        normalized_limit = max(0, min(sample_limit, 100))
        conn = self._ensure_conn()
        count_row = conn.execute("SELECT COUNT(*) FROM pending").fetchone()
        pending_count = int(count_row[0] if count_row else 0)
        if normalized_limit == 0:
            return PendingSnapshot(count=pending_count, sample=[])
        rows = conn.execute(
            "SELECT file FROM pending ORDER BY file LIMIT ?",
            (normalized_limit,),
        ).fetchall()
        return PendingSnapshot(
            count=pending_count, sample=[str(row[0]) for row in rows]
        )

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
        # SQLite substr() is 1-indexed; +1 starts right after the key prefix.
        prefix_len = len(key_prefix) + 1
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

    def get_term_counts_for_source_prefixes(
        self, key_prefix: str, source_prefixes: list[str]
    ) -> list[tuple[str, int, int]]:
        """Return (term, chunk_count, doc_count) for sources under any prefix.

        Like ``get_term_counts_by_scope`` but matches on source path instead of
        the stored scope column, enabling umbrella-scope aggregation where
        files are indexed under more-specific leaf scopes.
        """
        if not source_prefixes:
            return []
        conn = self._ensure_conn()
        prefix_len = len(key_prefix) + 1
        like_pattern = f"{key_prefix}%"
        source_clauses = " OR ".join("source LIKE ?" for _ in source_prefixes)
        source_params = [f"{p}%" for p in source_prefixes]
        rows = conn.execute(
            f"SELECT substr(key, ?),"
            f" COUNT(DISTINCT chunk_id),"
            f" COUNT(DISTINCT CASE WHEN source != '' THEN source END)"
            f" FROM properties"
            f" WHERE key LIKE ? AND source != '' AND ({source_clauses})"
            f" GROUP BY substr(key, ?)"
            f" ORDER BY COUNT(DISTINCT chunk_id) DESC",
            (prefix_len, like_pattern, *source_params, prefix_len),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def count_docs_for_prefixes(self, source_prefixes: list[str]) -> int:
        """Count distinct source documents matching any source prefix."""
        if not source_prefixes:
            return 0
        conn = self._ensure_conn()
        clauses = " OR ".join("source LIKE ?" for _ in source_prefixes)
        params = [f"{p}%" for p in source_prefixes]
        row = conn.execute(
            f"SELECT COUNT(DISTINCT source) FROM properties"
            f" WHERE source != '' AND ({clauses})",
            params,
        ).fetchone()
        return row[0] if row else 0

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

    def list_known_sources(self, prefixes: list[str]) -> set[str]:
        """Return source paths present in metadata-only tables under watched prefixes."""
        if not prefixes:
            return set()

        conn = self._ensure_conn()
        sources: set[str] = set()
        queries = (
            "SELECT DISTINCT source FROM failed_extractions",
            "SELECT DISTINCT source_path AS source FROM articles",
        )
        for sql in queries:
            for (candidate,) in conn.execute(sql).fetchall():
                if any(candidate.startswith(prefix) for prefix in prefixes):
                    sources.add(candidate)
        return sources

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

    def has_retriable_failures(self, source: str) -> bool:
        """Return True if source has any non-permanent failed extraction rows."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT 1 FROM failed_extractions"
            " WHERE source = ? AND permanent = 0 LIMIT 1",
            (source,),
        ).fetchone()
        return row is not None

    def get_failure_snapshot(self) -> FailureSnapshot:
        """Return failed extraction counts used by operational status endpoints."""
        return FailureSnapshot(
            failed_extractions_count=self.get_failed_count(),
            failed_extractions_permanent_count=self.get_permanent_count(),
        )

    def get_article_row(self, source_path: str) -> dict[str, str] | None:
        """Return one article row by exact source path."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT source_path, filename, title, authors, venue, published_date, "
            "doi, abstract, scope, content_hash, subdirectory, comments "
            "FROM articles WHERE source_path = ?",
            (source_path,),
        ).fetchone()
        if row is None:
            return None
        return {
            "source_path": _row_str(row[0]),
            "filename": _row_str(row[1]),
            "title": _row_str(row[2]),
            "authors": _row_str(row[3]),
            "venue": _row_str(row[4]),
            "published_date": _row_str(row[5]),
            "doi": _row_str(row[6]),
            "abstract": _row_str(row[7]),
            "scope": _row_str(row[8]),
            "content_hash": _row_str(row[9]),
            "subdirectory": _row_str(row[10]),
            "comments": _row_str(row[11]),
        }

    def find_latest_article_by_filename(
        self,
        filename: str,
        *,
        exclude_source_path: str | None = None,
    ) -> dict[str, str] | None:
        """Return the newest surviving row for a basename, optionally excluding one path."""
        conn = self._ensure_conn()
        sql = (
            "SELECT source_path, filename, title, authors, venue, published_date, "
            "doi, abstract, scope, content_hash, subdirectory, comments "
            "FROM articles WHERE filename = ?"
        )
        params: list[str] = [filename]
        if exclude_source_path is not None:
            sql += " AND source_path != ?"
            params.append(exclude_source_path)
        sql += " ORDER BY updated_at DESC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return {
            "source_path": _row_str(row[0]),
            "filename": _row_str(row[1]),
            "title": _row_str(row[2]),
            "authors": _row_str(row[3]),
            "venue": _row_str(row[4]),
            "published_date": _row_str(row[5]),
            "doi": _row_str(row[6]),
            "abstract": _row_str(row[7]),
            "scope": _row_str(row[8]),
            "content_hash": _row_str(row[9]),
            "subdirectory": _row_str(row[10]),
            "comments": _row_str(row[11]),
        }

    def find_orphaned_article_by_hash(
        self,
        *,
        content_hash: str,
        new_source_path: str,
    ) -> dict[str, str] | None:
        """Return a missing-on-disk article row with matching content hash."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT source_path, filename, title, authors, venue, published_date, "
            "doi, abstract, scope, content_hash, subdirectory, comments "
            "FROM articles WHERE content_hash = ? AND source_path != ? "
            "ORDER BY updated_at DESC",
            (content_hash, new_source_path),
        ).fetchall()
        for row in rows:
            if not Path(str(row[0])).exists():
                return {
                    "source_path": _row_str(row[0]),
                    "filename": _row_str(row[1]),
                    "title": _row_str(row[2]),
                    "authors": _row_str(row[3]),
                    "venue": _row_str(row[4]),
                    "published_date": _row_str(row[5]),
                    "doi": _row_str(row[6]),
                    "abstract": _row_str(row[7]),
                    "scope": _row_str(row[8]),
                    "content_hash": _row_str(row[9]),
                    "subdirectory": _row_str(row[10]),
                    "comments": _row_str(row[11]),
                }
        return None

    async def move_article_source_path(
        self,
        *,
        old_source_path: str,
        new_source_path: str,
        new_filename: str,
        new_scope: str,
        new_subdirectory: str,
    ) -> bool:
        """Move one article row to a new source path without touching curated fields."""

        async def _write() -> bool:
            conn = self._ensure_conn()
            if conn.execute(
                "SELECT 1 FROM articles WHERE source_path = ?",
                (new_source_path,),
            ).fetchone():
                return False
            cursor = conn.execute(
                "UPDATE articles SET "
                "source_path = ?, filename = ?, scope = ?, subdirectory = ?, "
                "updated_at = datetime('now') "
                "WHERE source_path = ?",
                (
                    new_source_path,
                    new_filename,
                    new_scope,
                    new_subdirectory,
                    old_source_path,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

        return await self._seq.run(_write())

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

    def lookup_articles_by_hash(self, hashes: list[str]) -> dict[str, ArticleEntry]:
        """Batch-lookup articles by content_hash. Returns {hash: ArticleEntry}."""
        conn = self._ensure_conn()
        if not hashes:
            return {}
        placeholders = ",".join("?" for _ in hashes)
        rows = conn.execute(
            "SELECT content_hash, title, authors, venue, published_date, doi, abstract, subdirectory"
            f" FROM articles WHERE content_hash IN ({placeholders})",
            hashes,
        ).fetchall()
        return {
            row[0]: ArticleEntry(
                title=row[1],
                authors=row[2],
                venue=row[3],
                published_date=row[4],
                doi=row[5],
                abstract=row[6],
                subdirectory=row[7],
                content_hash=row[0],
            )
            for row in rows
        }

    def rescope_all(self, scope_resolver: Callable[[str], str]) -> tuple[int, int]:
        """Re-resolve scope for all entries using the given resolver.

        Resolver runs only for rows with a non-empty ``source`` field.
        Empty-source rows are legacy/unbackfilled entries and are skipped.

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
