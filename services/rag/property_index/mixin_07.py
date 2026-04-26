"""_PropertyIndexPart07 — PropertyIndex method chunk (SLOC split)."""
from __future__ import annotations

from ._spec import *  # noqa: F401,F403

class _PropertyIndexPart07:
    def list_contextualization_exceptions(
        self, *, limit: int = 100
    ) -> list[ContextualizationException]:
        """Return recent degraded contextualization attempts."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT id, source, source_hash, contextualize_model, operation_id,"
            " total_chunks, cache_miss_chunks, successful_chunks, failed_chunks,"
            " abandoned_chunks, abandoned_indices_json, request_ids_json,"
            " first_failure, idle_seconds, tail_idle_timeout_s, recorded_at"
            " FROM contextualization_exceptions"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            ContextualizationException(
                id=int(row[0]),
                source=row[1],
                source_hash=row[2],
                contextualize_model=row[3],
                operation_id=row[4],
                total_chunks=int(row[5]),
                cache_miss_chunks=int(row[6]),
                successful_chunks=int(row[7]),
                failed_chunks=int(row[8]),
                abandoned_chunks=int(row[9]),
                abandoned_indices=[int(idx) for idx in json.loads(row[10])],
                request_ids={
                    int(idx): request_id
                    for idx, request_id in json.loads(row[11]).items()
                },
                first_failure=row[12],
                idle_seconds=row[13],
                tail_idle_timeout_s=row[14],
                recorded_at=row[15],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Extraction queue (async decoupled extraction)
    # ------------------------------------------------------------------

    async def enqueue_extraction(self, source: str) -> None:
        """Add a source to the extraction queue (idempotent)."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "INSERT OR IGNORE INTO extraction_queue (source) VALUES (?)",
                (source,),
            )
            conn.commit()

        await self._seq.run(_write())

    async def dequeue_extraction(
        self, limit: int = 10, max_attempts: int = 5
    ) -> list[str]:
        """Return sources ready for extraction (backoff elapsed, under max attempts).

        Backoff: attempt 0 → immediate, 1 → 1 min, 2 → 5 min, 3 → 15 min, 4+ → 60 min.
        """
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT source FROM extraction_queue"
            " WHERE attempts < ?"
            "   AND (last_attempt_at IS NULL"
            "     OR datetime(last_attempt_at,"
            "       '+' || CASE"
            "         WHEN attempts <= 1 THEN '1'"
            "         WHEN attempts <= 2 THEN '5'"
            "         WHEN attempts <= 3 THEN '15'"
            "         ELSE '60'"
            "       END || ' minutes') < datetime('now'))"
            " ORDER BY queued_at ASC LIMIT ?",
            (max_attempts, limit),
        ).fetchall()
        return [row[0] for row in rows]

    async def complete_extraction(self, source: str) -> None:
        """Remove a source from the extraction queue after successful extraction."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM extraction_queue WHERE source = ?", (source,))
            conn.commit()

        await self._seq.run(_write())

    async def fail_extraction(
        self,
        source: str,
        *,
        increment_attempt: bool = True,
    ) -> None:
        """Record a failed extraction attempt; always refresh retry timestamp.

        ``attempts`` is the source-level retry budget. Capacity-class failures
        MUST pass ``increment_attempt=False`` so transient infrastructure pressure
        does not consume the source defect budget.
        """
        attempt_increment = 1 if increment_attempt else 0

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "UPDATE extraction_queue"
                " SET attempts = attempts + ?, last_attempt_at = datetime('now')"
                " WHERE source = ?",
                (attempt_increment, source),
            )
            conn.commit()

        await self._seq.run(_write())

    def get_extraction_queue_count(self) -> int:
        """Return the number of sources pending extraction."""
        conn = self._ensure_conn()
        return conn.execute("SELECT COUNT(*) FROM extraction_queue").fetchone()[0]

    def get_indexed_source_count(self) -> int:
        """Return the number of sources committed to ChromaDB (embed complete).

        ∀ source ∈ indexed_sources: ChromaDB upsert completed for that source.
        This count includes sources with extraction failures (they are embedded
        but pending re-extraction) and excludes sources where the process was
        interrupted before upsert_indexed_source was called.
        """
        conn = self._ensure_conn()
        return conn.execute("SELECT COUNT(*) FROM indexed_sources").fetchone()[0]

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


