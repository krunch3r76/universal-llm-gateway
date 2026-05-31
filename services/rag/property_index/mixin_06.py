"""_PropertyIndexPart06 — PropertyIndex method chunk (SLOC split)."""

# ruff: noqa: F405 — names supplied by `from ._spec import *` split-module pattern.
from __future__ import annotations

from ._spec import *  # noqa: F401,F403


class _PropertyIndexPart06:
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
        """Return True if source has non-permanent failures whose backoff has elapsed.

        Backoff schedule (from recorded_at): attempt 1 → 5 min, 2 → 15 min,
        3 → 60 min, 4+ → 240 min. Prevents failing files from hammering
        every sweep.
        """
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT 1 FROM failed_extractions"
            " WHERE source = ? AND permanent = 0"
            "   AND datetime(recorded_at,"
            "     '+' || CASE"
            "       WHEN attempt_count <= 1 THEN '5'"
            "       WHEN attempt_count <= 2 THEN '15'"
            "       WHEN attempt_count <= 3 THEN '60'"
            "       ELSE '240'"
            "     END || ' minutes') < datetime('now')"
            " LIMIT 1",
            (source,),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # File-level indexing failures (permanent vs transient)
    # ------------------------------------------------------------------

    async def record_indexing_failure(
        self,
        *,
        source: str,
        failure_category: str,
        failure_reason: str,
        error_message: str,
        error_type: str,
        source_hash: str | None,
        source_size_bytes: int | None,
        source_mtime_ns: int | None,
    ) -> int:
        """Upsert a file-level failure row. Returns the resulting attempt_count.

        ``first_failed_at`` is intentionally NOT in the ON CONFLICT SET clause
        so the original first-failure timestamp is preserved across retries.
        """

        async def _write() -> int:
            conn = self._ensure_conn()
            row = conn.execute(
                "INSERT INTO indexing_failures ("
                "  source, failure_category, failure_reason, error_message, error_type,"
                "  source_hash, source_size_bytes, source_mtime_ns"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(source) DO UPDATE SET"
                "  failure_category = excluded.failure_category,"
                "  failure_reason   = excluded.failure_reason,"
                "  error_message    = excluded.error_message,"
                "  error_type       = excluded.error_type,"
                "  source_hash      = excluded.source_hash,"
                "  source_size_bytes = excluded.source_size_bytes,"
                "  source_mtime_ns  = excluded.source_mtime_ns,"
                "  last_failed_at   = datetime('now'),"
                "  attempt_count    = attempt_count + 1"
                " RETURNING attempt_count",
                (
                    source,
                    failure_category,
                    failure_reason,
                    error_message,
                    error_type,
                    source_hash,
                    source_size_bytes,
                    source_mtime_ns,
                ),
            ).fetchone()
            conn.commit()
            return int(row[0]) if row else 1

        return await self._seq.run(_write())

    async def clear_indexing_failure(self, source: str) -> bool:
        """Delete one indexing_failures row. Returns True iff a row existed."""

        async def _write() -> bool:
            conn = self._ensure_conn()
            cursor = conn.execute(
                "DELETE FROM indexing_failures WHERE source = ?", (source,)
            )
            conn.commit()
            return cursor.rowcount > 0

        return await self._seq.run(_write())

    def get_indexing_failure(self, source: str) -> IndexingFailure | None:
        """Return the indexing_failures row for *source* if present."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT source, failure_category, failure_reason, error_message,"
            " error_type, first_failed_at, last_failed_at, attempt_count,"
            " source_hash, source_size_bytes, source_mtime_ns"
            " FROM indexing_failures WHERE source = ?",
            (source,),
        ).fetchone()
        if row is None:
            return None
        return IndexingFailure(
            source=row[0],
            failure_category=row[1],
            failure_reason=row[2],
            error_message=row[3],
            error_type=row[4],
            first_failed_at=row[5],
            last_failed_at=row[6],
            attempt_count=int(row[7]),
            source_hash=row[8],
            source_size_bytes=row[9],
            source_mtime_ns=row[10],
        )

    def list_indexing_failures(
        self, *, category: str | None = None
    ) -> list[IndexingFailure]:
        """List indexing_failures rows, optionally filtered by category."""
        conn = self._ensure_conn()
        if category is None:
            rows = conn.execute(
                "SELECT source, failure_category, failure_reason, error_message,"
                " error_type, first_failed_at, last_failed_at, attempt_count,"
                " source_hash, source_size_bytes, source_mtime_ns"
                " FROM indexing_failures ORDER BY last_failed_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT source, failure_category, failure_reason, error_message,"
                " error_type, first_failed_at, last_failed_at, attempt_count,"
                " source_hash, source_size_bytes, source_mtime_ns"
                " FROM indexing_failures WHERE failure_category = ?"
                " ORDER BY last_failed_at DESC",
                (category,),
            ).fetchall()
        return [
            IndexingFailure(
                source=r[0],
                failure_category=r[1],
                failure_reason=r[2],
                error_message=r[3],
                error_type=r[4],
                first_failed_at=r[5],
                last_failed_at=r[6],
                attempt_count=int(r[7]),
                source_hash=r[8],
                source_size_bytes=r[9],
                source_mtime_ns=r[10],
            )
            for r in rows
        ]

    def is_indexing_failure_invalidated_by_content(
        self, source: str, mtime_ns: int, size_bytes: int
    ) -> bool:
        """True iff a row exists AND its stored mtime/size differ from current."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT source_mtime_ns, source_size_bytes"
            " FROM indexing_failures WHERE source = ?",
            (source,),
        ).fetchone()
        if row is None:
            return False
        return row[0] != mtime_ns or row[1] != size_bytes

    def get_indexing_failure_counts(self) -> tuple[int, int]:
        """Return (permanent_count, transient_count) for status endpoints."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT failure_category, COUNT(*) FROM indexing_failures"
            " GROUP BY failure_category"
        ).fetchall()
        permanent = 0
        transient = 0
        for category, count in rows:
            if category == "permanent":
                permanent = int(count)
            elif category == "transient":
                transient = int(count)
        return permanent, transient

    async def record_contextualization_exception(
        self,
        *,
        source: str,
        source_hash: str | None,
        contextualize_model: str,
        operation_id: str | None,
        total_chunks: int,
        cache_miss_chunks: int,
        successful_chunks: int,
        failed_chunks: int,
        abandoned_indices: list[int],
        request_ids: dict[int, str],
        first_failure: str,
        idle_seconds: float | None,
        tail_idle_timeout_s: float | None,
    ) -> int:
        """Persist a degraded contextualization attempt without blocking indexing."""

        async def _write() -> int:
            conn = self._ensure_conn()
            row = conn.execute(
                "INSERT INTO contextualization_exceptions ("
                "  source, source_hash, contextualize_model, operation_id,"
                "  total_chunks, cache_miss_chunks, successful_chunks,"
                "  failed_chunks, abandoned_chunks, abandoned_indices_json,"
                "  request_ids_json, first_failure, idle_seconds, tail_idle_timeout_s"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " RETURNING id",
                (
                    source,
                    source_hash,
                    contextualize_model,
                    operation_id,
                    total_chunks,
                    cache_miss_chunks,
                    successful_chunks,
                    failed_chunks,
                    len(abandoned_indices),
                    json.dumps(abandoned_indices),
                    json.dumps(request_ids, sort_keys=True),
                    first_failure[:500],
                    idle_seconds,
                    tail_idle_timeout_s,
                ),
            ).fetchone()
            conn.commit()
            return int(row[0]) if row else 0

        return await self._seq.run(_write())
