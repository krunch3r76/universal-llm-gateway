"""_PropertyIndexPart07 — contextualization exceptions and extraction queue."""

from __future__ import annotations

from ._spec import (
    ContextualizationException,
    ExtractionQueueBreakdown,
    ExtractionQueueClaim,
    ExtractionQueueRow,
    RecoveredExtractionClaim,
    json,
)


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
    ) -> list[ExtractionQueueClaim]:
        """Atomically claim sources ready for extraction.

        ``last_attempt_at`` is the claim marker. ``last_failure_at`` is the
        retry/backoff marker. A row is in flight iff:

        last_attempt_at IS NOT NULL ∧
        (last_failure_at IS NULL ∨ last_failure_at < last_attempt_at)
        """

        async def _write() -> list[ExtractionQueueClaim]:
            conn = self._ensure_conn()
            rows = conn.execute(
                "WITH candidates AS ("
                " SELECT source FROM extraction_queue"
                " WHERE attempts < ?"
                "   AND (last_attempt_at IS NULL"
                "     OR (last_failure_at IS NOT NULL"
                "       AND datetime(last_failure_at) >= datetime(last_attempt_at)"
                "       AND datetime(last_failure_at,"
                "         '+' || CASE"
                "           WHEN attempts <= 1 THEN '1'"
                "           WHEN attempts <= 2 THEN '5'"
                "           WHEN attempts <= 3 THEN '15'"
                "           ELSE '60'"
                "         END || ' minutes') < datetime('now')))"
                " ORDER BY queued_at ASC LIMIT ?"
                ")"
                " UPDATE extraction_queue"
                " SET last_attempt_at = datetime('now')"
                " WHERE source IN (SELECT source FROM candidates)"
                " RETURNING source, attempts, queued_at, last_attempt_at",
                (max_attempts, limit),
            ).fetchall()
            conn.commit()
            return [
                ExtractionQueueClaim(
                    source=row[0],
                    attempts=int(row[1]),
                    queued_at=row[2],
                    claimed_at=row[3],
                )
                for row in rows
            ]

        return await self._seq.run(_write())

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
        failure_category: str,
        error: str,
        error_type: str,
    ) -> None:
        """Record a failed extraction attempt and release the source claim.

        ``attempts`` is the source-level retry budget. ``last_attempt_at`` is
        claim-only and MUST NOT be updated here; ``last_failure_at`` is the
        backoff anchor.
        """
        attempt_increment = 1 if increment_attempt else 0

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "UPDATE extraction_queue"
                " SET attempts = attempts + ?,"
                " last_error = ?,"
                " last_error_type = ?,"
                " last_failure_category = ?,"
                " last_failure_at = datetime('now')"
                " WHERE source = ?",
                (
                    attempt_increment,
                    error[:1000],
                    error_type,
                    failure_category,
                    source,
                ),
            )
            conn.commit()

        await self._seq.run(_write())

    async def recover_abandoned_extraction_claims(
        self,
    ) -> list[RecoveredExtractionClaim]:
        """Clear in-flight claims from a previous RAG process.

        Called before starting the extraction worker. The service has one
        extraction worker, so any in-flight row present at process startup is
        necessarily from a prior process.
        """

        async def _write() -> list[RecoveredExtractionClaim]:
            conn = self._ensure_conn()
            rows = conn.execute(
                "SELECT source, attempts, queued_at, last_attempt_at,"
                " (julianday('now') - julianday(last_attempt_at)) * 86400.0"
                " FROM extraction_queue"
                " WHERE last_attempt_at IS NOT NULL"
                "   AND (last_failure_at IS NULL"
                "     OR datetime(last_failure_at) < datetime(last_attempt_at))"
                " ORDER BY queued_at ASC"
            ).fetchall()
            conn.execute(
                "UPDATE extraction_queue"
                " SET last_attempt_at = NULL"
                " WHERE last_attempt_at IS NOT NULL"
                "   AND (last_failure_at IS NULL"
                "     OR datetime(last_failure_at) < datetime(last_attempt_at))"
            )
            conn.commit()
            return [
                RecoveredExtractionClaim(
                    source=row[0],
                    attempts=int(row[1]),
                    queued_at=row[2],
                    claimed_at=row[3],
                    claimed_age_seconds=float(row[4] or 0.0),
                )
                for row in rows
            ]

        return await self._seq.run(_write())

    def get_extraction_queue_breakdown(
        self, *, max_attempts: int = 5
    ) -> ExtractionQueueBreakdown:
        """Return extraction queue counts by operational state."""
        rows = self.list_extraction_queue_rows(max_attempts=max_attempts, limit=None)
        ready = sum(1 for row in rows if row.state == "ready")
        in_flight = sum(1 for row in rows if row.state == "in_flight")
        cooling_off = sum(1 for row in rows if row.state == "cooling_off")
        exhausted = sum(1 for row in rows if row.state == "exhausted")
        capacity_blocked = sum(
            1
            for row in rows
            if row.state == "cooling_off" and row.last_failure_category == "capacity"
        )
        return ExtractionQueueBreakdown(
            total=len(rows),
            ready=ready,
            in_flight=in_flight,
            cooling_off=cooling_off,
            capacity_blocked=capacity_blocked,
            exhausted=exhausted,
        )

    def list_extraction_queue_rows(
        self, *, max_attempts: int = 5, limit: int | None = 100
    ) -> list[ExtractionQueueRow]:
        """Return source-level queue rows with computed state."""
        conn = self._ensure_conn()
        limit_sql = "" if limit is None else " LIMIT ?"
        params: tuple[int, ...] = (
            (max_attempts, max_attempts, limit)
            if limit is not None
            else (max_attempts, max_attempts)
        )
        rows = conn.execute(
            "SELECT source, queued_at, attempts, last_attempt_at, last_error,"
            " last_error_type, last_failure_category, last_failure_at,"
            " CASE"
            "   WHEN attempts >= ? THEN 'exhausted'"
            "   WHEN last_attempt_at IS NOT NULL"
            "     AND (last_failure_at IS NULL"
            "       OR datetime(last_failure_at) < datetime(last_attempt_at))"
            "     THEN 'in_flight'"
            "   WHEN last_failure_at IS NOT NULL"
            "     AND datetime(last_failure_at,"
            "       '+' || CASE"
            "         WHEN attempts <= 1 THEN '1'"
            "         WHEN attempts <= 2 THEN '5'"
            "         WHEN attempts <= 3 THEN '15'"
            "         ELSE '60'"
            "       END || ' minutes') >= datetime('now')"
            "     AND attempts < ?"
            "     THEN 'cooling_off'"
            "   ELSE 'ready'"
            " END AS queue_state"
            " FROM extraction_queue"
            " ORDER BY queued_at ASC"
            f"{limit_sql}",
            params,
        ).fetchall()
        return [
            ExtractionQueueRow(
                source=row[0],
                queued_at=row[1],
                attempts=int(row[2]),
                last_attempt_at=row[3],
                last_error=row[4],
                last_error_type=row[5],
                last_failure_category=row[6],
                last_failure_at=row[7],
                state=row[8],
            )
            for row in rows
        ]

    def get_extraction_queue_count(self) -> int:
        """Return the number of sources pending extraction."""
        conn = self._ensure_conn()
        return conn.execute("SELECT COUNT(*) FROM extraction_queue").fetchone()[0]
