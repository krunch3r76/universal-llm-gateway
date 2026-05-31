"""_PropertyIndexPart04 — PropertyIndex method chunk (SLOC split)."""

# ruff: noqa: F405 — names supplied by `from ._spec import *` split-module pattern.
from __future__ import annotations

from ._spec import *  # noqa: F401,F403


class _PropertyIndexPart04:
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

        deduped_rows = list(dict.fromkeys(rows))

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                placeholders = ",".join("?" for _ in scope_names)
                conn.execute(
                    f"DELETE FROM scope_vocabulary WHERE scope IN ({placeholders})",
                    scope_names,
                )
                if deduped_rows:
                    conn.executemany(
                        "INSERT INTO scope_vocabulary (scope, register, term)"
                        " VALUES (?, ?, ?)",
                        deduped_rows,
                    )
                conn.execute("COMMIT")
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception("replace_scope_vocabulary_for_scopes failed: %s", e)
                raise

        await self._seq.run(_write())

    def has_scope_vocabulary(self, scope: str) -> bool:
        """Return True when at least one vocabulary row exists for *scope*."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT 1 FROM scope_vocabulary WHERE scope = ? LIMIT 1",
            (scope,),
        ).fetchone()
        return row is not None

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
            conn.execute("DELETE FROM scope_freshness WHERE scope = ?", (scope,))
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
            " extraction_model, updated_at, source_hash"
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
            source_hash=_row_str(row[6]),
        )

    def get_cached_contexts(
        self,
        *,
        source_hash: str,
        chunk_hashes: list[str],
        contextualize_model: str,
        contextualize_schema_version: str,
    ) -> dict[str, str]:
        """Return cached context prefixes for the subset of chunk hashes matching all invalidators.

        Synchronous — matches PropertyIndex read convention; safe under single-writer.
        Returns an empty dict when source_hash is the V11 unknown-identity sentinel ('').
        Batches the IN clause at _SQLITE_MAX_VARIABLES (999 SQLite limit minus four fixed params).
        """
        if not chunk_hashes or not source_hash:
            return {}

        conn = self._ensure_conn()
        out: dict[str, str] = {}
        for start in range(0, len(chunk_hashes), _SQLITE_MAX_VARIABLES):
            batch = chunk_hashes[start : start + _SQLITE_MAX_VARIABLES]
            placeholders = ",".join("?" for _ in batch)
            sql = (
                "SELECT chunk_hash, context_prefix FROM contextualized_chunks "
                "WHERE source_hash = ? "
                "AND contextualize_model = ? "
                "AND contextualize_schema_version = ? "
                f"AND chunk_hash IN ({placeholders})"
            )
            params = [
                source_hash,
                contextualize_model,
                contextualize_schema_version,
                *batch,
            ]
            for row in conn.execute(sql, params):
                out[str(row[0])] = _row_str(row[1])
        return out

    async def store_cached_contexts(
        self,
        *,
        source_hash: str,
        contextualize_model: str,
        contextualize_schema_version: str,
        entries: list[StoredContextRow],
    ) -> int:
        """Persist non-empty context prefixes idempotently; returns rows written.

        `entries` MUST come from `build_stored_context_rows(...)` which already
        filters empty context_prefix and empty chunk_hash. The defensive
        re-filter below catches mis-use; the V10 CHECK constraint is the
        final backstop. Returns 0 when source_hash is the V11 unknown-identity
        sentinel (''). Async because writes serialize through SequentialExecutor.
        """
        if not source_hash or not entries:
            return 0
        filtered = [row for row in entries if row.context_prefix and row.chunk_hash]
        if not filtered:
            return 0

        async def _write() -> int:
            conn = self._ensure_conn()
            rows = [
                (
                    source_hash,
                    row.chunk_hash,
                    contextualize_model,
                    contextualize_schema_version,
                    row.context_prefix,
                )
                for row in filtered
            ]
            conn.executemany(
                "INSERT INTO contextualized_chunks ("
                "  source_hash, chunk_hash, contextualize_model,"
                "  contextualize_schema_version, context_prefix"
                ") VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source_hash, chunk_hash, contextualize_model, "
                "            contextualize_schema_version) "
                "DO UPDATE SET "
                "  context_prefix = excluded.context_prefix, "
                "  cached_at = datetime('now')",
                rows,
            )
            conn.commit()
            return len(rows)

        return await self._seq.run(_write())

    async def delete_cached_contexts_for_source_hash(self, source_hash: str) -> int:
        """Remove cache rows for one content identity; returns rows deleted."""
        if not source_hash:
            return 0

        async def _write() -> int:
            conn = self._ensure_conn()
            cursor = conn.execute(
                "DELETE FROM contextualized_chunks WHERE source_hash = ?",
                (source_hash,),
            )
            conn.commit()
            return int(cursor.rowcount)

        return await self._seq.run(_write())
