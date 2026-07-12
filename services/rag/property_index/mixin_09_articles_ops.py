"""_PropertyIndexPart09 — articles, indexed-source counts, failure snapshots."""

from __future__ import annotations

from ._spec import (
    FailureSnapshot,
    Path,
    _row_str,
    defaultdict,
)


class _PropertyIndexPart09:
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

    def resolve_source_paths(
        self,
        *,
        source_paths: list[str] | None = None,
        arxiv_ids: list[str] | None = None,
        filenames: list[str] | None = None,
    ) -> list[str]:
        """Resolve status-query identifiers to canonical source_path values."""
        resolved: list[str] = []
        seen: set[str] = set()

        def add(path: str) -> None:
            if path and path not in seen:
                seen.add(path)
                resolved.append(path)

        for source_path in source_paths or []:
            add(source_path)

        conn = self._ensure_conn()
        for filename in filenames or []:
            for row in conn.execute(
                "SELECT source_path FROM articles WHERE filename = ?",
                (filename,),
            ).fetchall():
                add(_row_str(row[0]))
            suffix = f"/{filename}"
            for row in conn.execute(
                "SELECT source FROM indexed_sources WHERE source LIKE ?",
                (f"%{suffix}",),
            ).fetchall():
                path = _row_str(row[0])
                if path.endswith(filename):
                    add(path)

        for arxiv_id in arxiv_ids or []:
            bare = arxiv_id.strip()
            normalized = bare.replace("/", "-")
            for pattern in (f"%{bare}%", f"%arxiv-{normalized}%"):
                for row in conn.execute(
                    "SELECT source_path FROM articles"
                    " WHERE source_path LIKE ? OR filename LIKE ?",
                    (pattern, pattern),
                ).fetchall():
                    add(_row_str(row[0]))

        return resolved

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

    def get_source_pipeline_state(self, source_path: str) -> dict:
        """Batch-read pipeline state for one source from SQLite.

        Returns {is_indexed, queue_row, contextualized_chunks} where:
        - is_indexed: source is committed to ChromaDB (in indexed_sources)
        - queue_row: dict with 'state' if in extraction_queue, else None
        - contextualized_chunks: count of cached context rows via articles.content_hash join

        queue_state CASE mirrors list_extraction_queue_rows (max_attempts=5).
        capacity_blocked is a sub-state of cooling_off where last_failure_category='capacity'.
        """
        _max_attempts = 5
        conn = self._ensure_conn()
        is_row = conn.execute(
            "SELECT 1 FROM indexed_sources WHERE source = ?", (source_path,)
        ).fetchone()
        eq_row = conn.execute(
            "SELECT"
            " CASE"
            "   WHEN attempts >= ? THEN 'exhausted'"
            "   WHEN last_attempt_at IS NOT NULL"
            "     AND (last_failure_at IS NULL"
            "       OR datetime(last_failure_at) < datetime(last_attempt_at))"
            "     THEN 'in_flight'"
            "   WHEN last_failure_at IS NOT NULL"
            "     AND datetime(last_failure_at, '+1 minutes') >= datetime('now')"
            "     AND attempts < ?"
            "     AND last_failure_category = 'capacity'"
            "     THEN 'capacity_blocked'"
            "   WHEN last_failure_at IS NOT NULL"
            "     AND datetime(last_failure_at, '+1 minutes') >= datetime('now')"
            "     AND attempts < ?"
            "     THEN 'cooling_off'"
            "   ELSE 'ready'"
            " END"
            " FROM extraction_queue WHERE source = ?",
            (_max_attempts, _max_attempts, _max_attempts, source_path),
        ).fetchone()
        ctx_count: int = conn.execute(
            "SELECT COUNT(*) FROM contextualized_chunks cc"
            " JOIN articles a ON cc.source_hash = a.content_hash"
            " WHERE a.source_path = ? AND a.content_hash != ''",
            (source_path,),
        ).fetchone()[0]
        return {
            "is_indexed": is_row is not None,
            "queue_row": {"state": eq_row[0]} if eq_row else None,
            "contextualized_chunks": ctx_count,
        }

    def get_source_item_data(self, source_path: str) -> dict:
        """Return per-source data for SourceStatusItem in one DB pass.

        Returns {is_indexed, indexed_at, queue_row, contextualized_chunks}.
        queue_row includes state, attempts, last_error, and position (1-based)
        when present, else None. indexed_at is updated_at from indexed_sources.
        """
        _max_attempts = 5
        conn = self._ensure_conn()
        is_row = conn.execute(
            "SELECT updated_at FROM indexed_sources WHERE source = ?",
            (source_path,),
        ).fetchone()
        eq_row = conn.execute(
            "SELECT attempts, last_error,"
            " CASE"
            "   WHEN attempts >= ? THEN 'exhausted'"
            "   WHEN last_attempt_at IS NOT NULL"
            "     AND (last_failure_at IS NULL"
            "       OR datetime(last_failure_at) < datetime(last_attempt_at))"
            "     THEN 'in_flight'"
            "   WHEN last_failure_at IS NOT NULL"
            "     AND datetime(last_failure_at, '+1 minutes') >= datetime('now')"
            "     AND attempts < ?"
            "     AND last_failure_category = 'capacity'"
            "     THEN 'capacity_blocked'"
            "   WHEN last_failure_at IS NOT NULL"
            "     AND datetime(last_failure_at, '+1 minutes') >= datetime('now')"
            "     AND attempts < ?"
            "     THEN 'cooling_off'"
            "   ELSE 'ready'"
            " END"
            " FROM extraction_queue WHERE source = ?",
            (_max_attempts, _max_attempts, _max_attempts, source_path),
        ).fetchone()
        ctx_count: int = conn.execute(
            "SELECT COUNT(*) FROM contextualized_chunks cc"
            " JOIN articles a ON cc.source_hash = a.content_hash"
            " WHERE a.source_path = ? AND a.content_hash != ''",
            (source_path,),
        ).fetchone()[0]
        queue_position: int | None = None
        if eq_row is not None:
            pos_row = conn.execute(
                "SELECT COUNT(*) FROM extraction_queue"
                " WHERE queued_at <= (SELECT queued_at FROM extraction_queue WHERE source = ?)",
                (source_path,),
            ).fetchone()
            queue_position = pos_row[0] if pos_row else None
        return {
            "is_indexed": is_row is not None,
            "indexed_at": is_row[0] if is_row else None,
            "queue_row": {
                "attempts": eq_row[0],
                "last_error": eq_row[1],
                "state": eq_row[2],
                "position": queue_position,
            }
            if eq_row is not None
            else None,
            "contextualized_chunks": ctx_count,
        }

    def count_scopes_with_stale_corpus_hints(self) -> int:
        """Count scopes whose corpus_hints were updated after the last vocabulary classification.

        ∀ scope ∈ scope_freshness: counts those where ∃ corpus_hints row with created_at >
        classified_at. This is a superset of what classify_vocabulary.py considers stale
        (the script also gates on files_hash equality). Use for monitoring; not a reliable
        classify-readiness gate.
        """
        conn = self._ensure_conn()
        return conn.execute(
            "SELECT COUNT(*) FROM scope_freshness sf"
            " WHERE EXISTS ("
            "   SELECT 1 FROM corpus_hints ch"
            "   WHERE ch.scope = sf.scope"
            "     AND ch.created_at > sf.classified_at"
            ")"
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
