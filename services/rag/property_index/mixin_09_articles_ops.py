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
