"""_PropertyIndexPart07 — contextualization exceptions and extraction queue.

SLOC-split mixin composed into ``PropertyIndex``. Lists degraded
contextualization attempts and implements the durable ``extraction_queue``:
enqueue, claim-based ``dequeue_extraction`` used by ``extraction/worker_loop.py``,
complete/fail (``last_failure_at`` anchors retry backoff),
``wake_extraction_queue`` (reset cooling-off rows when the extraction model
becomes available), per-claim active execution IDs, and ``recover_abandoned_extraction_claims`` which
``extraction_runtime.start_extraction_runtime`` runs at startup to release claims
orphaned by a crash. Also exposes queue breakdown, row listing and count reads.
"""

from __future__ import annotations

from ._spec import (
    ContextualizationException,
    ExtractionQueueBreakdown,
    ExtractionQueueClaim,
    ExtractionQueueResetRow,
    ExtractionQueueRow,
    RecoveredExtractionClaim,
    json,
)
from .queue_policy import (
    ExhaustedRedrive,
    arm_backoff_minutes,
    claim_filter_sql,
    disposition,
    is_dead_letter,
    queue_state_case_sql,
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

    async def wake_extraction_queue(self) -> int:
        """Reset cooling-off items that failed due to model unavailability.

        Clears ``last_failure_at`` for non-capacity cooling-off items so they
        become immediately eligible on the next ``dequeue_extraction`` call.
        Called when the extraction pipeline transitions to available — items
        that were cooling off because the model was unreachable should retry
        promptly rather than waiting for the backoff window.

        Returns the number of rows reset.
        """

        async def _write() -> int:
            conn = self._ensure_conn()
            cursor = conn.execute(
                "UPDATE extraction_queue"
                " SET last_failure_at = NULL"
                " WHERE last_failure_at IS NOT NULL"
                "   AND (last_failure_category IS NULL"
                "     OR last_failure_category != 'capacity')",
            )
            conn.commit()
            return cursor.rowcount

        return await self._seq.run(_write())

    async def reset_extraction_queue_sources(
        self, sources: list[str]
    ) -> tuple[list[ExtractionQueueResetRow], int]:
        """Reset exhausted queue rows for named sources (no insert on missing)."""

        async def _write() -> tuple[list[ExtractionQueueResetRow], int]:
            conn = self._ensure_conn()
            results: list[ExtractionQueueResetRow] = []
            reset_count = 0
            for source in sources:
                row = conn.execute(
                    "SELECT attempts FROM extraction_queue WHERE source = ?",
                    (source,),
                ).fetchone()
                if row is None:
                    results.append(
                        ExtractionQueueResetRow(
                            source=source,
                            outcome="not_found",
                            attempts=None,
                        )
                    )
                    continue
                previous_attempts = int(row[0])
                if previous_attempts < 5:
                    results.append(
                        ExtractionQueueResetRow(
                            source=source,
                            outcome="not_exhausted",
                            attempts=previous_attempts,
                        )
                    )
                    continue
                cursor = conn.execute(
                    "UPDATE extraction_queue"
                    " SET attempts = 0,"
                    " last_attempt_at = NULL,"
                    " last_failure_at = NULL,"
                    " active_execution_id = NULL,"
                    " last_error = NULL,"
                    " last_error_type = NULL,"
                    " last_failure_category = NULL,"
                    " queued_at = datetime('now')"
                    " WHERE source = ? AND attempts >= 5",
                    (source,),
                )
                if cursor.rowcount > 0:
                    reset_count += 1
                    results.append(
                        ExtractionQueueResetRow(
                            source=source,
                            outcome="reset",
                            attempts=0,
                            previous_attempts=previous_attempts,
                        )
                    )
                else:
                    results.append(
                        ExtractionQueueResetRow(
                            source=source,
                            outcome="not_exhausted",
                            attempts=previous_attempts,
                        )
                    )
            conn.commit()
            return results, reset_count

        return await self._seq.run(_write())

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
                f" WHERE {claim_filter_sql()}"
                " ORDER BY queued_at ASC LIMIT ?"
                ")"
                " UPDATE extraction_queue"
                " SET last_attempt_at = datetime('now')"
                " WHERE source IN (SELECT source FROM candidates)"
                " RETURNING source, attempts, queued_at, last_attempt_at,"
                " active_execution_id",
                (max_attempts, limit),
            ).fetchall()
            conn.commit()
            return [
                ExtractionQueueClaim(
                    source=row[0],
                    attempts=int(row[1]),
                    queued_at=row[2],
                    claimed_at=row[3],
                    active_execution_id=row[4],
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

    async def clear_extraction_execution_id(self, source: str) -> None:
        """Clear the active_execution_id once a pipeline terminates."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "UPDATE extraction_queue SET active_execution_id = NULL WHERE source = ?",
                (source,),
            )
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
            current = conn.execute(
                "SELECT attempts, last_failure_category FROM extraction_queue"
                " WHERE source = ?",
                (source,),
            ).fetchone()
            previous = int(current[0]) if current is not None else 0
            previous_category = current[1] if current is not None else None
            attempts_after = previous + attempt_increment
            category = failure_category
            if previous_category == "dead_letter" or is_dead_letter(
                attempts_after, error, failure_category
            ):
                category = "dead_letter"
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
                    category,
                    source,
                ),
            )
            conn.commit()

        await self._seq.run(_write())

    async def record_extraction_execution_id(
        self, source: str, execution_id: str
    ) -> None:
        """Store the Stargate async execution_id for an in-flight extraction claim."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute(
                "UPDATE extraction_queue SET active_execution_id = ? WHERE source = ?",
                (execution_id, source),
            )
            conn.commit()

        await self._seq.run(_write())

    async def recover_abandoned_extraction_claims(
        self,
    ) -> list[RecoveredExtractionClaim]:
        """Clear in-flight claims from a previous RAG process.

        Called before starting the extraction worker. The service has one
        extraction worker, so any in-flight row present at process startup is
        necessarily from a prior process. Returns active_execution_id so the
        caller can cancel the orphaned Stargate execution before re-queuing.
        """

        async def _write() -> list[RecoveredExtractionClaim]:
            conn = self._ensure_conn()
            rows = conn.execute(
                "SELECT source, attempts, queued_at, last_attempt_at,"
                " (julianday('now') - julianday(last_attempt_at)) * 86400.0,"
                " active_execution_id"
                " FROM extraction_queue"
                " WHERE last_attempt_at IS NOT NULL"
                "   AND (last_failure_at IS NULL"
                "     OR datetime(last_failure_at) < datetime(last_attempt_at))"
                " ORDER BY queued_at ASC"
            ).fetchall()
            conn.execute(
                "UPDATE extraction_queue"
                " SET last_attempt_at = NULL, active_execution_id = NULL"
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
                    active_execution_id=row[5],
                )
                for row in rows
            ]

        return await self._seq.run(_write())

    async def redrive_exhausted_once(self, *, dry_run: bool) -> ExhaustedRedrive:
        """Arm one claim for retriable exhausted rows, or persist dead_letter.

        Does not reset ``attempts`` and does not run extraction. In-flight rows
        are left alone. ``dry_run`` reports the same classification without
        writing.
        """
        rows = self.list_extraction_queue_rows(limit=None)
        retried: list[str] = []
        dead_lettered: list[str] = []
        for row in rows:
            if row.state == "in_flight":
                continue
            kind = disposition(row.attempts, row.last_error, row.last_failure_category)
            if kind == "retry":
                retried.append(row.source)
            elif kind == "dead_letter":
                dead_lettered.append(row.source)

        if not dry_run and (retried or dead_lettered):

            async def _write() -> None:
                conn = self._ensure_conn()
                for source in dead_lettered:
                    conn.execute(
                        "UPDATE extraction_queue"
                        " SET last_failure_category = 'dead_letter'"
                        " WHERE source = ?",
                        (source,),
                    )
                for source in retried:
                    current = conn.execute(
                        "SELECT attempts FROM extraction_queue WHERE source = ?",
                        (source,),
                    ).fetchone()
                    if current is None:
                        continue
                    minutes = arm_backoff_minutes(int(current[0]))
                    conn.execute(
                        "UPDATE extraction_queue SET"
                        " last_attempt_at = datetime('now', ?),"
                        " last_failure_at = datetime('now', ?),"
                        " active_execution_id = NULL"
                        " WHERE source = ?",
                        (
                            f"-{minutes + 1} minutes",
                            f"-{minutes} minutes",
                            source,
                        ),
                    )
                conn.commit()

            await self._seq.run(_write())

        return ExhaustedRedrive(
            dry_run=dry_run,
            retried=tuple(retried),
            dead_lettered=tuple(dead_lettered),
        )

    def get_extraction_queue_breakdown(
        self, *, max_attempts: int = 5
    ) -> ExtractionQueueBreakdown:
        """Return extraction queue counts by operational state."""
        rows = self.list_extraction_queue_rows(max_attempts=max_attempts, limit=None)
        ready = sum(1 for row in rows if row.state == "ready")
        in_flight = sum(1 for row in rows if row.state == "in_flight")
        cooling_off = sum(
            1 for row in rows if row.state in ("cooling_off", "capacity_blocked")
        )
        exhausted = sum(1 for row in rows if row.state in ("exhausted", "dead_letter"))
        capacity_blocked = sum(1 for row in rows if row.state == "capacity_blocked")
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
        """Return source-level queue rows with computed state.

        ``max_attempts`` is retained for callers. Thresholds are
        ``QUEUE_FAST_ATTEMPTS`` and ``QUEUE_RETRY_CEILING`` in
        ``queue_policy``.
        """
        del max_attempts
        conn = self._ensure_conn()
        limit_sql = "" if limit is None else " LIMIT ?"
        params: tuple[int, ...] = (limit,) if limit is not None else ()
        case_sql = queue_state_case_sql()
        rows = conn.execute(
            "SELECT source, queued_at, attempts, last_attempt_at, last_error,"
            " last_error_type, last_failure_category, last_failure_at,"
            f"{case_sql} AS queue_state"
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
