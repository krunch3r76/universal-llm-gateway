"""_PropertyIndexPart05 — PropertyIndex method chunk (SLOC split)."""

# ruff: noqa: F405 — names supplied by `from ._spec import *` split-module pattern.
from __future__ import annotations

from ._spec import *  # noqa: F401,F403


class _PropertyIndexPart05:
    async def garbage_collect_contextualized_chunks(self) -> int:
        """Remove cache rows whose source no longer appears in indexed_sources.

        G1 rows are keyed by ``source_identity`` (resolved path). Legacy V10 rows
        remain keyed by ``source_hash`` for dual-read until fallback retires.
        Rows in ``indexed_sources`` with ``source_hash = ''`` (V11 sentinel) are
        NOT considered live hash references for the legacy table.
        """

        async def _write() -> int:
            conn = self._ensure_conn()
            g1 = conn.execute(
                "DELETE FROM contextualized_chunks_g1 "
                "WHERE source_identity NOT IN ("
                "  SELECT source FROM indexed_sources"
                ")"
            )
            legacy = conn.execute(
                "DELETE FROM contextualized_chunks "
                "WHERE source_hash <> '' "
                "AND source_hash NOT IN ("
                "  SELECT source_hash FROM indexed_sources WHERE source_hash <> ''"
                ")"
            )
            conn.commit()
            return int(g1.rowcount) + int(legacy.rowcount)

        return await self._seq.run(_write())

    def count_contextualized_chunks(self) -> int:
        """Return total cached context prefix rows for operator capacity reporting."""
        conn = self._ensure_conn()
        g1 = conn.execute("SELECT COUNT(*) FROM contextualized_chunks_g1").fetchone()
        legacy = conn.execute("SELECT COUNT(*) FROM contextualized_chunks").fetchone()
        return int(g1[0] if g1 else 0) + int(legacy[0] if legacy else 0)

    def get_indexed_source_hash(self, source: str) -> str | None:
        """Return the currently recorded source_hash for an indexed source path, or None."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT source_hash FROM indexed_sources WHERE source = ?",
            (source,),
        ).fetchone()
        if row is None:
            return None
        value = _row_str(row[0])
        return value or None

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

    def get_term_counts_by_source(
        self, key_prefix: str, source_prefixes: list[str]
    ) -> list[tuple[str, str, int, int]]:
        """Return (source, term, chunk_count, doc_count) grouped by source and term.

        Like ``get_term_counts_for_source_prefixes`` but retains per-source
        attribution instead of aggregating across sources.
        """
        if not source_prefixes:
            return []
        conn = self._ensure_conn()
        prefix_len = len(key_prefix) + 1
        like_pattern = f"{key_prefix}%"
        source_clauses = " OR ".join("source LIKE ?" for _ in source_prefixes)
        source_params = [f"{p}%" for p in source_prefixes]
        rows = conn.execute(
            f"SELECT source, substr(key, ?),"
            f" COUNT(DISTINCT chunk_id),"
            f" COUNT(DISTINCT CASE WHEN source != '' THEN source END)"
            f" FROM properties"
            f" WHERE key LIKE ? AND source != '' AND ({source_clauses})"
            f" GROUP BY source, substr(key, ?)"
            f" ORDER BY COUNT(DISTINCT chunk_id) DESC",
            (prefix_len, like_pattern, *source_params, prefix_len),
        ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

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

    def get_indexed_sources(self, prefix: str | None = None) -> list[str]:
        """Return cached indexed source paths, optionally filtered by prefix."""
        conn = self._ensure_conn()
        if prefix:
            rows = conn.execute(
                "SELECT source FROM indexed_sources WHERE source LIKE ? ORDER BY source",
                (f"{prefix}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT source FROM indexed_sources ORDER BY source"
            ).fetchall()
        return [r[0] for r in rows]

    def get_indexed_sources_with_timestamps(
        self, prefix: str | None = None
    ) -> dict[str, str]:
        """Return cached indexed source paths → updated_at timestamps from SQLite.

        More efficient than scanning ChromaDB chunk metadata for recency.
        Returns {source_path: updated_at_iso_str}.
        """
        conn = self._ensure_conn()
        if prefix:
            rows = conn.execute(
                "SELECT source, updated_at FROM indexed_sources"
                " WHERE source LIKE ? ORDER BY source",
                (f"{prefix}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT source, updated_at FROM indexed_sources ORDER BY source"
            ).fetchall()
        return {r[0]: r[1] for r in rows if r[1]}

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
                "SELECT chunk_id, source, error, parse_failure_reason, attempt_count,"
                " permanent, recorded_at"
                " FROM failed_extractions WHERE source = ?"
                " ORDER BY recorded_at DESC",
                (source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT chunk_id, source, error, parse_failure_reason, attempt_count,"
                " permanent, recorded_at"
                " FROM failed_extractions ORDER BY recorded_at DESC"
            ).fetchall()
        return [
            FailedChunk(
                chunk_id=r[0],
                source=r[1],
                error=r[2],
                parse_failure_reason=r[3],
                attempt_count=r[4],
                permanent=bool(r[5]),
                recorded_at=r[6],
            )
            for r in rows
        ]
