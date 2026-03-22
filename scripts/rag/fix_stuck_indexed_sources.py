#!/usr/bin/env python3
"""Detect and purge indexed_sources rows lacking associated ChromaDB chunks.

One-time operational repair for files orphaned by the upsert_indexed_source
poisoning bug (fixed in indexing.py). Files in indexed_sources but absent
from ChromaDB are permanently skipped by the stat-first unchanged check -
deleting their indexed_sources rows forces the watcher to re-index them on
its next sweep.

Run AFTER deploying the code fix. Ideally with indexing paused (stop the RAG
watcher or run during maintenance). Use --dry-run first to preview changes.

Usage:
    python scripts/rag/fix_stuck_indexed_sources.py --dry-run
    python scripts/rag/fix_stuck_indexed_sources.py --apply
    python scripts/rag/fix_stuck_indexed_sources.py --apply --prefix /path/to/scope/
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path.home() / ".rag" / "store" / "rag_metadata.db"
_DEFAULT_CHROMA_DIR = Path.home() / ".rag" / "store"


def _get_chroma_sources(store_path: Path) -> set[str]:
    """Return distinct source paths present in ChromaDB's knowledge collection.

    Initializes a read-only ChromaDB persistent client and scans chunk metadata
    for source fields. This is an O(N) scan over all chunks - acceptable for a
    one-time repair script but not for hot-path use.
    """
    import chromadb
    from chromadb.errors import NotFoundError

    client = chromadb.PersistentClient(path=str(store_path))
    try:
        collection = client.get_collection("knowledge")
    except NotFoundError:
        logger.error("ChromaDB collection 'knowledge' not found at %s", store_path)
        return set()
    except Exception as exc:
        logger.error(
            "Unexpected error reading ChromaDB collection 'knowledge' at %s: %s",
            store_path,
            exc,
        )
        return set()
    all_data = collection.get(include=["metadatas"])
    sources: set[str] = set()
    for meta in all_data.get("metadatas") or []:
        src = meta.get("source") if isinstance(meta, dict) else None
        if isinstance(src, str) and src:
            sources.add(src)
    return sources


def find_orphaned_sources(
    *,
    db_path: Path = _DEFAULT_DB,
    chroma_path: Path = _DEFAULT_CHROMA_DIR,
    prefix: str | None = None,
) -> list[str]:
    """Compare indexed_sources against ChromaDB and return orphaned source paths.

    A source is orphaned when it appears in indexed_sources (the stat-first skip
    cache) but has zero chunks in ChromaDB. These files will never be re-indexed
    because the skip check sees them as already processed.
    """
    if not db_path.exists():
        logger.error("Metadata DB not found: %s", db_path)
        return []

    chroma_sources = _get_chroma_sources(chroma_path)
    logger.info("ChromaDB distinct sources: %d", len(chroma_sources))

    with contextlib.closing(
        sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ) as conn:
        if prefix:
            rows = conn.execute(
                "SELECT source FROM indexed_sources WHERE source LIKE ?",
                (f"{prefix}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT source FROM indexed_sources").fetchall()

    indexed = [r[0] for r in rows]
    orphaned = [s for s in indexed if s not in chroma_sources]
    return orphaned


def purge_orphaned_sources(
    orphaned: list[str],
    *,
    db_path: Path = _DEFAULT_DB,
) -> int:
    """Delete indexed_sources rows for orphaned files so the watcher re-processes.

    Also clears stale failed_extractions rows for these sources, since the chunks
    they referenced no longer exist. Returns the count of deleted rows.
    """
    if not orphaned:
        return 0

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        deleted = 0
        for source in orphaned:
            try:
                cursor = conn.execute(
                    "DELETE FROM indexed_sources WHERE source = ?", (source,)
                )
                conn.execute(
                    "DELETE FROM failed_extractions WHERE source = ?", (source,)
                )
                conn.commit()
                deleted += cursor.rowcount
            except sqlite3.OperationalError as exc:
                logger.error(
                    "Failed to delete indexed_sources row for %s: %s", source, exc
                )
                conn.rollback()
    return deleted


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Detect and repair orphaned indexed_sources entries"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run", action="store_true", help="List orphaned files without modifying"
    )
    group.add_argument(
        "--apply", action="store_true", help="Delete orphaned indexed_sources rows"
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        help="Only check sources matching this path prefix",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(_DEFAULT_DB),
        help=f"Path to rag_metadata.db (default: {_DEFAULT_DB})",
    )
    parser.add_argument(
        "--chroma",
        type=str,
        default=str(_DEFAULT_CHROMA_DIR),
        help=f"Path to ChromaDB store directory (default: {_DEFAULT_CHROMA_DIR})",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    chroma_path = Path(args.chroma)

    orphaned = find_orphaned_sources(
        db_path=db_path, chroma_path=chroma_path, prefix=args.prefix
    )

    if not orphaned:
        print("No orphaned indexed_sources entries found.")
        return

    print(f"\nFound {len(orphaned)} orphaned indexed_sources entries:")
    for src in sorted(orphaned):
        print(f"  {src}")

    if args.dry_run:
        print(f"\n[dry-run] Would delete {len(orphaned)} indexed_sources rows.")
        print("Re-run with --apply to execute.")
        return

    deleted = purge_orphaned_sources(orphaned, db_path=db_path)
    print(f"\nDeleted {deleted} indexed_sources rows.")
    print("Restart the RAG service to trigger re-indexing of these files.")


if __name__ == "__main__":
    main()
