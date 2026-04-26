"""_PropertyIndexPart02 — PropertyIndex method chunk (SLOC split)."""
from __future__ import annotations

from ._spec import *  # noqa: F401,F403

class _PropertyIndexPart02:
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
        source_hash: str = "",
    ) -> None:
        """Record committed source state for stat-first unchanged skip checks.

        Invariant: callers MUST only invoke this after the source's chunks have
        been committed to ChromaDB (or verified to already exist during recovery).
        Writing this row without committed chunks creates a permanently orphaned
        file — the stat-first skip (get_indexed_source + has_retriable_failures)
        will skip re-indexing on every subsequent sweep.

        ¬call on extraction failure paths where ChromaDB upsert was not reached.
        """

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT INTO indexed_sources ("
                "  source, mtime_ns, size_bytes, extraction_schema_version,"
                "  extraction_model, source_hash, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, datetime('now'))"
                " ON CONFLICT(source) DO UPDATE SET"
                "  mtime_ns = excluded.mtime_ns,"
                "  size_bytes = excluded.size_bytes,"
                "  extraction_schema_version = excluded.extraction_schema_version,"
                "  extraction_model = excluded.extraction_model,"
                "  source_hash = excluded.source_hash,"
                "  updated_at = datetime('now')",
                (
                    source,
                    mtime_ns,
                    size_bytes,
                    extraction_schema_version,
                    extraction_model,
                    source_hash,
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
        self,
        chunk_id: str,
        source: str,
        error: str,
        *,
        parse_failure_reason: str | None = None,
        permanent: bool = False,
        increment_attempt: bool = True,
    ) -> None:
        """Record an extraction failure.

        When ``increment_attempt`` is True (default), ``attempt_count``
        increases monotonically toward ``max_extraction_attempts``.
        Infrastructure failures (queue timeout, capacity exhaustion) should
        pass ``increment_attempt=False`` so transient Stargate unavailability
        does not burn the retry budget meant for genuine extraction errors.

        permanent=True marks the chunk as permanently abandoned.
        Once permanent=1, it is never reset to 0.
        """
        attempt_increment = 1 if increment_attempt else 0

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT INTO failed_extractions"
                " (chunk_id, source, error, parse_failure_reason, attempt_count, permanent)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(chunk_id) DO UPDATE SET"
                "   error = excluded.error,"
                "   parse_failure_reason = excluded.parse_failure_reason,"
                "   attempt_count = attempt_count + ?,"
                "   permanent = MAX(permanent, excluded.permanent),"
                "   recorded_at = datetime('now')",
                (
                    chunk_id,
                    source,
                    error,
                    parse_failure_reason,
                    attempt_increment,
                    permanent,
                    attempt_increment,
                ),
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


