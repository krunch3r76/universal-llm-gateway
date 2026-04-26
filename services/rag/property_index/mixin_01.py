"""_PropertyIndexPart01 — PropertyIndex method chunk (SLOC split)."""
from __future__ import annotations

from ._spec import *  # noqa: F401,F403

class _PropertyIndexPart01:
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
        if "parse_failure_reason" not in failed_cols:
            conn.execute(
                "ALTER TABLE failed_extractions ADD COLUMN parse_failure_reason TEXT"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_failed_permanent ON failed_extractions(permanent)"
        )

    def _migration_v2_metadata(self, conn: sqlite3.Connection) -> None:
        """Create normalized metadata tables used by dual-write generators."""
        conn.executescript(_V2_METADATA_SQL)

    def _migration_v11_indexed_sources_source_hash(
        self, conn: sqlite3.Connection
    ) -> None:
        """Add source_hash column so cache GC can resolve content identity by source path."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(indexed_sources)")}
        if "source_hash" not in columns:
            conn.execute(
                "ALTER TABLE indexed_sources "
                "ADD COLUMN source_hash TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_indexed_sources_source_hash "
            "ON indexed_sources(source_hash)"
        )

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

        def _migration_v7_parse_failure_reason(conn: sqlite3.Connection) -> None:
            """Adds per-chunk parse failure detail to failed extraction rows."""
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(failed_extractions)")
            }
            if "parse_failure_reason" not in cols:
                conn.execute(
                    "ALTER TABLE failed_extractions "
                    "ADD COLUMN parse_failure_reason TEXT"
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
            (
                7,
                "failed_extractions.parse_failure_reason for parser diagnostics",
                _migration_v7_parse_failure_reason,
            ),
            (
                8,
                "extraction_queue for async decoupled extraction",
                lambda conn: conn.executescript(_V8_EXTRACTION_QUEUE_SQL),
            ),
            (
                9,
                "indexing_failures: file-level permanent vs transient failure memory",
                lambda conn: conn.executescript(_V9_INDEXING_FAILURES_SQL),
            ),
            (
                10,
                "contextualize cache: per-chunk prefix reuse across retries",
                lambda conn: conn.executescript(_V10_CONTEXTUALIZED_CHUNKS_SQL),
            ),
            (
                11,
                "indexed_sources.source_hash for cache cleanup resolution",
                self._migration_v11_indexed_sources_source_hash,
            ),
            (
                12,
                "contextualization_exceptions: durable partial-context diagnostics",
                lambda conn: conn.executescript(_V12_CONTEXTUALIZATION_EXCEPTIONS_SQL),
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


