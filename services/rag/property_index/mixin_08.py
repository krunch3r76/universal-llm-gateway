"""_PropertyIndexPart08 — PropertyIndex method chunk (SLOC split)."""

# ruff: noqa: F405 — names supplied by `from ._spec import *` split-module pattern.
from __future__ import annotations

from ._spec import *  # noqa: F401,F403


class _PropertyIndexPart08:
    def lookup_articles_by_hash(self, hashes: list[str]) -> dict[str, ArticleEntry]:
        """Batch-lookup articles by content_hash. Returns {hash: ArticleEntry}."""
        conn = self._ensure_conn()
        if not hashes:
            return {}
        placeholders = ",".join("?" for _ in hashes)
        rows = conn.execute(
            "SELECT content_hash, title, authors, venue, published_date, doi, abstract, subdirectory"
            f" FROM articles WHERE content_hash IN ({placeholders})",
            hashes,
        ).fetchall()
        return {
            row[0]: ArticleEntry(
                title=row[1],
                authors=row[2],
                venue=row[3],
                published_date=row[4],
                doi=row[5],
                abstract=row[6],
                subdirectory=row[7],
                content_hash=row[0],
            )
            for row in rows
        }

    def rescope_all(self, scope_resolver: Callable[[str], str]) -> tuple[int, int]:
        """Re-resolve scope for all entries using the given resolver.

        Resolver runs only for rows with a non-empty ``source`` field.
        Empty-source rows are legacy/unbackfilled entries and are skipped.

        Returns (total_entries, updated_count). Uses a single transaction
        for atomicity.
        """
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT rowid, key, chunk_id, scope, source FROM properties"
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for rowid, key, chunk_id, old_scope, source in rows:
            # Entries with empty source are legacy/unbackfilled rows.
            # They are intentionally skipped to avoid resolver behavior ambiguity.
            if not source:
                continue
            new_scope = scope_resolver(source)
            if new_scope != old_scope:
                updates.append((new_scope, rowid))
        if updates:
            conn.executemany("UPDATE properties SET scope = ? WHERE rowid = ?", updates)
            conn.commit()
        return len(rows), len(updates)
