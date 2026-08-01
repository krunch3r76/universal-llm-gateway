"""_PropertyIndexPart03 — PropertyIndex method chunk (SLOC split)."""

# ruff: noqa: F405 — names supplied by `from ._spec import *` split-module pattern.
from __future__ import annotations

from ._spec import *  # noqa: F401,F403


class _PropertyIndexPart03:
    async def sync_article_structural_fields(
        self,
        *,
        source_path: str,
        filename: str,
        content_hash: str,
        scope: str,
        subdirectory: str,
    ) -> bool:
        """Create or refresh non-curated article identity fields.

        Returns True when a new row was created. Existing rows keep curated fields
        like title/authors/venue while structural fields stay aligned with the
        latest indexed file state.
        """

        async def _write() -> bool:
            conn = self._ensure_conn()
            existing = conn.execute(
                "SELECT 1 FROM articles WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            conn.execute(
                "INSERT INTO articles ("
                "  source_path, filename, scope, content_hash, subdirectory, updated_at"
                ") VALUES (?, ?, ?, ?, ?, datetime('now'))"
                " ON CONFLICT(source_path) DO UPDATE SET"
                "  filename = excluded.filename,"
                "  scope = excluded.scope,"
                "  content_hash = excluded.content_hash,"
                "  subdirectory = excluded.subdirectory,"
                "  updated_at = datetime('now')",
                (source_path, filename, scope, content_hash, subdirectory),
            )
            conn.commit()
            return existing is None

        return await self._seq.run(_write())

    async def remove_article(self, source_path: str) -> bool:
        """Remove the articles row for a source path. Returns True if a row existed."""

        async def _write() -> bool:
            conn = self._ensure_conn()
            cursor = conn.execute(
                "DELETE FROM articles WHERE source_path = ?", (source_path,)
            )
            conn.commit()
            return cursor.rowcount > 0

        return await self._seq.run(_write())

    async def remove_articles_by_prefix(self, prefix: str) -> int:
        """Remove all articles rows whose source_path starts with *prefix*."""

        async def _write() -> int:
            conn = self._ensure_conn()
            cursor = conn.execute(
                "DELETE FROM articles WHERE source_path LIKE ? ESCAPE '\\'",
                (prefix.replace("%", "\\%").replace("_", "\\_") + "%",),
            )
            conn.commit()
            return cursor.rowcount

        return await self._seq.run(_write())

    async def remove_source_metadata(
        self,
        source: str,
        chunk_ids: list[str] | None = None,
        *,
        remove_article: bool = True,
    ) -> None:
        """Remove source-scoped SQLite metadata for one source.

        Watcher cleanup may preserve the article row so a later move-detection or
        re-index path can reuse curated metadata. Admin delete paths keep the
        default `remove_article=True` and remain fully destructive.
        """
        normalized_chunk_ids = list(dict.fromkeys(chunk_ids or []))

        async def _write() -> None:
            conn = self._ensure_conn()
            # MUST capture source_hash BEFORE deleting indexed_sources row —
            # otherwise the cache cleanup below has no key to use, and orphan
            # rows wait for startup GC.
            source_hash_row = conn.execute(
                "SELECT source_hash FROM indexed_sources WHERE source = ?",
                (source,),
            ).fetchone()
            resolved_source_hash = (
                _row_str(source_hash_row[0]) if source_hash_row else ""
            )
            conn.execute("DELETE FROM failed_extractions WHERE source = ?", (source,))
            if remove_article:
                conn.execute("DELETE FROM articles WHERE source_path = ?", (source,))
            conn.execute("DELETE FROM indexed_sources WHERE source = ?", (source,))
            if resolved_source_hash:
                conn.execute(
                    "DELETE FROM contextualized_chunks WHERE source_hash = ?",
                    (resolved_source_hash,),
                )
            if normalized_chunk_ids:
                placeholders = ",".join("?" for _ in normalized_chunk_ids)
                conn.execute(
                    f"DELETE FROM properties WHERE chunk_id IN ({placeholders})",
                    normalized_chunk_ids,
                )
            conn.commit()

        await self._seq.run(_write())
        if normalized_chunk_ids:
            await self.fts.remove_batch(normalized_chunk_ids)

    async def clear_failures_for(self, source: str) -> None:
        """Remove all failure records for a source file (e.g. after successful recovery)."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM failed_extractions WHERE source = ?", (source,))
            conn.commit()

        await self._seq.run(_write())

    async def clear_failures_for_ids(self, source: str, chunk_ids: list[str]) -> None:
        """Remove failure records for the given chunk IDs of a source file.

        Used when a partial write succeeds: clear only the chunks that were
        written so failed chunks retain their attempt count for retry.
        """

        async def _write() -> None:
            conn = self._ensure_conn()
            if not chunk_ids:
                return
            placeholders = ",".join("?" * len(chunk_ids))
            conn.execute(
                "DELETE FROM failed_extractions WHERE source = ? AND chunk_id IN ("
                + placeholders
                + ")",
                (source, *chunk_ids),
            )
            conn.commit()

        await self._seq.run(_write())

    async def backfill_source(self, chunk_to_source: dict[str, str]) -> int:
        """Set source for rows where source is empty. Returns count updated."""

        async def _write() -> int:
            conn = self._ensure_conn()
            if not chunk_to_source:
                return 0
            batch = [(src, cid) for cid, src in chunk_to_source.items() if src]
            cursor = conn.executemany(
                "UPDATE properties SET source = ? WHERE chunk_id = ? AND source = ''",
                batch,
            )
            conn.commit()
            return cursor.rowcount

        return await self._seq.run(_write())

    async def replace_corpus_hints_rows(
        self, rows: list[tuple[str, str, float, str]]
    ) -> None:
        """Atomically replace all corpus hints rows used for dual-write parity.

        Uses an explicit transaction (BEGIN IMMEDIATE/COMMIT) to avoid a visible
        empty-table window for readers while replacing table contents.
        """

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM corpus_hints")
                if rows:
                    conn.executemany(
                        "INSERT INTO corpus_hints (scope, term, score, prefix)"
                        " VALUES (?, ?, ?, ?)",
                        rows,
                    )
                conn.execute("COMMIT")
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception("replace_corpus_hints_rows failed: %s", e)
                raise

        await self._seq.run(_write())

    async def replace_corpus_hints_for_scope(
        self, scope: str, rows: list[tuple[str, str, float, str]]
    ) -> None:
        """Atomically replace corpus hints for a single scope.

        Deletes only the rows matching *scope*, then inserts the new rows.
        Other scopes' hints remain untouched.
        """

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM corpus_hints WHERE scope = ?", (scope,))
                if rows:
                    conn.executemany(
                        "INSERT INTO corpus_hints (scope, term, score, prefix)"
                        " VALUES (?, ?, ?, ?)",
                        rows,
                    )
                conn.execute("COMMIT")
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception(
                    "replace_corpus_hints_for_scope(%s) failed: %s", scope, e
                )
                raise

        await self._seq.run(_write())

    async def replace_scope_vocabulary(
        self, vocabulary: dict[str, dict[str, list[str]]]
    ) -> None:
        """Atomically replace scope vocabulary rows from register-structured scope-term payload maps."""
        rows: list[tuple[str, str, str]] = []
        for scope, registers in sorted(vocabulary.items()):
            for register, terms in sorted(registers.items()):
                for term in terms:
                    normalized = term.strip()
                    if normalized:
                        rows.append((scope, register, normalized))

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM scope_vocabulary")
                if rows:
                    conn.executemany(
                        "INSERT INTO scope_vocabulary (scope, register, term)"
                        " VALUES (?, ?, ?)",
                        rows,
                    )
                conn.execute("COMMIT")
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception("replace_scope_vocabulary failed: %s", e)
                raise

        await self._seq.run(_write())

    async def replace_skill_vocabulary(
        self, rows: list[tuple[str, str, str, float, int]]
    ) -> None:
        """Atomically replace all skill_vocabulary rows (idempotent full-replace)."""

        async def _write() -> None:
            conn = self._ensure_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM skill_vocabulary")
                if rows:
                    conn.executemany(
                        "INSERT INTO skill_vocabulary"
                        " (slug, register, term, score, chunk_count)"
                        " VALUES (?, ?, ?, ?, ?)",
                        rows,
                    )
                conn.execute("COMMIT")
            except sqlite3.Error as e:
                conn.execute("ROLLBACK")
                logger.exception("replace_skill_vocabulary failed: %s", e)
                raise

        await self._seq.run(_write())

    def load_skill_vocabulary(self, slug: str | None = None) -> list[tuple[str, str]]:
        """Return (register, term) rows for *slug*, or all rows when slug is None."""
        conn = self._ensure_conn()
        if slug is not None:
            rows = conn.execute(
                "SELECT register, term FROM skill_vocabulary"
                " WHERE slug = ?"
                " ORDER BY score DESC, register ASC, term ASC",
                (slug,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT register, term FROM skill_vocabulary"
                " ORDER BY slug ASC, score DESC, register ASC, term ASC"
            ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def load_scope_vocabulary_for_scope(self, scope: str) -> list[tuple[str, str]]:
        """Return (register, term) rows for one scope from scope_vocabulary."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT register, term FROM scope_vocabulary"
            " WHERE scope = ?"
            " ORDER BY register ASC, term ASC",
            (scope,),
        ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def load_corpus_hint_scores(self, scope: str) -> dict[str, float]:
        """Return term → score for one scope from corpus_hints."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT term, score FROM corpus_hints WHERE scope = ?",
            (scope,),
        ).fetchall()
        return {str(r[0]): float(r[1]) for r in rows}

    def scope_list_enrichment(self) -> dict[str, dict[str, object]]:
        """Return per-scope article_count and top_topics for GET /scopes."""
        conn = self._ensure_conn()
        result: dict[str, dict[str, object]] = {}
        for scope, article_count in conn.execute(
            "SELECT scope, COUNT(*) AS article_count FROM articles GROUP BY scope"
        ).fetchall():
            if isinstance(scope, str):
                result.setdefault(scope, {})["article_count"] = int(article_count)
        for scope, term in conn.execute(
            "SELECT scope, term FROM corpus_hints "
            "WHERE prefix = 'prop.topic@@' "
            "ORDER BY scope ASC, score DESC, term ASC"
        ).fetchall():
            if not (isinstance(scope, str) and isinstance(term, str)):
                continue
            topics = result.setdefault(scope, {}).setdefault("top_topics", [])
            if isinstance(topics, list) and len(topics) < 5 and term not in topics:
                topics.append(term)
        return result
