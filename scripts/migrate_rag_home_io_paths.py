"""One-time migration: rename /home/io/ → /mnt/torus/ in RAG stores.

Context: path normalization (PR feat/frontier-dispatch-strict-validation) fixed
what the watcher submits, but existing ChromaDB chunks still carry
source = "/home/io/...". The duplicate_pdf check compares hashes against stored
sources — same hash, different path → skip — freezing 46 PDF files permanently.

Run with the RAG service stopped:
    ~/.venvs/universal/bin/python3 scripts/migrate_rag_home_io_paths.py

Safe to re-run: all updates are idempotent (REPLACE on paths already correct is
a no-op; ChromaDB update on IDs without /home/io/ is a no-op).
"""

from __future__ import annotations

import sqlite3
import sys

import chromadb

OLD_PREFIX = "/home/io/"
NEW_PREFIX = "/mnt/torus/"

CHROMA_PATH = "/home/io/.rag/store"
SQLITE_PATH = "/home/io/.rag/store/rag_metadata.db"

COLLECTION_NAME = "knowledge"


def _replace_prefix(path: str) -> str:
    if path.startswith(OLD_PREFIX):
        return NEW_PREFIX + path[len(OLD_PREFIX) :]
    return path


def migrate_chromadb() -> int:
    """Update source metadata in ChromaDB for all /home/io/ entries.

    Returns the number of chunks updated.
    """
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception as exc:
        print(
            f"  ERROR: could not open collection '{COLLECTION_NAME}': {exc}",
            file=sys.stderr,
        )
        return 0

    total_chunks = collection.count()
    print(f"  ChromaDB '{COLLECTION_NAME}': {total_chunks} chunks total")

    # Collect all chunk IDs and metadata for /home/io/ sources.
    batch_size = 5000
    offset = 0
    stale_ids: list[str] = []
    stale_metadatas: list[dict] = []

    while True:
        results = collection.get(
            limit=batch_size,
            offset=offset,
            include=["metadatas"],
        )
        ids = results["ids"]
        if not ids:
            break
        for chunk_id, metadata in zip(ids, results["metadatas"]):
            if not isinstance(metadata, dict):
                continue
            src = metadata.get("source", "")
            if OLD_PREFIX in src:
                new_metadata = {**metadata, "source": _replace_prefix(src)}
                stale_ids.append(chunk_id)
                stale_metadatas.append(new_metadata)
        offset += len(ids)
        if len(ids) < batch_size:
            break

    if not stale_ids:
        print("  ChromaDB: no /home/io/ chunks found — already clean")
        return 0

    # Unique source files affected.
    affected_sources = sorted({m["source"] for m in stale_metadatas})
    print(
        f"  ChromaDB: {len(stale_ids)} chunks across {len(affected_sources)} source files"
    )

    # Update in batches of 500 (ChromaDB update limit guidance).
    update_batch = 500
    updated = 0
    for i in range(0, len(stale_ids), update_batch):
        batch_ids = stale_ids[i : i + update_batch]
        batch_metas = stale_metadatas[i : i + update_batch]
        collection.update(ids=batch_ids, metadatas=batch_metas)
        updated += len(batch_ids)
        print(f"  ChromaDB: updated {updated}/{len(stale_ids)} chunks...", flush=True)

    print(f"  ChromaDB: done — {updated} chunks updated")
    return updated


def _upsert_or_delete(
    conn: sqlite3.Connection,
    table: str,
    col: str,
    results: dict[str, int],
) -> None:
    """For a single-column unique-keyed table: update /home/io/ rows to /mnt/torus/
    when no canonical row exists yet; delete them when a canonical row already exists.

    This handles the case where path normalization created new /mnt/torus/ rows
    while old /home/io/ rows were not cleaned up.
    """
    # Fetch all stale rows.
    rows = conn.execute(
        f"SELECT rowid, {col} FROM {table} WHERE {col} LIKE ?",
        (f"%{OLD_PREFIX}%",),
    ).fetchall()

    deleted = updated = 0
    for row in rows:
        rowid, old_path = row[0], row[1]
        new_path = _replace_prefix(old_path)
        # Check if the canonical path already exists.
        exists = conn.execute(
            f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1",
            (new_path,),
        ).fetchone()
        if exists:
            # Canonical row is present — stale /home/io/ row is an orphan, delete it.
            conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
            deleted += 1
        else:
            conn.execute(
                f"UPDATE {table} SET {col} = ? WHERE rowid = ?",
                (new_path, rowid),
            )
            updated += 1

    results[table] = deleted + updated
    if deleted or updated:
        print(f"  SQLite {table}.{col}: {updated} updated, {deleted} deleted (orphans)")


def migrate_sqlite() -> dict[str, int]:
    """Update /home/io/ paths in all rag_metadata.db tables.

    For tables with unique constraints: UPDATE when canonical row is absent,
    DELETE (orphan) when it already exists.

    Returns a dict of {table: rows_affected}.
    """
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    results: dict[str, int] = {}

    try:
        # Tables with unique constraint on the path column: conditional upsert-or-delete.
        unique_tables = [
            ("indexed_sources", "source"),
            ("articles", "source_path"),
        ]
        for table, col in unique_tables:
            _upsert_or_delete(conn, table, col, results)

        # Tables without unique constraint on source — direct UPDATE is safe.
        bulk_updates = [
            ("properties", "source"),
            ("contextualization_exceptions", "source"),
            ("extraction_queue", "source"),
            ("indexing_failures", "source"),
        ]
        for table, col in bulk_updates:
            cur = conn.execute(
                f"UPDATE {table} SET {col} = REPLACE({col}, ?, ?) WHERE {col} LIKE ?",
                (OLD_PREFIX, NEW_PREFIX, f"%{OLD_PREFIX}%"),
            )
            results[table] = cur.rowcount
            if cur.rowcount:
                print(f"  SQLite {table}.{col}: {cur.rowcount} rows updated")

        # FTS5 shadow table — source is column c1 (UNINDEXED, safe to update directly).
        # chunk_id = c0, source = c1, content = c2.
        cur = conn.execute(
            "UPDATE chunks_fts_content SET c1 = REPLACE(c1, ?, ?) WHERE c1 LIKE ?",
            (OLD_PREFIX, NEW_PREFIX, f"%{OLD_PREFIX}%"),
        )
        results["chunks_fts_content"] = cur.rowcount
        if cur.rowcount:
            print(
                f"  SQLite chunks_fts_content.c1 (source): {cur.rowcount} rows updated"
            )

        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"  ERROR: SQLite migration failed: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()

    return results


def verify(expected_chroma_sources: set[str]) -> None:
    """Quick post-migration verification: confirm no /home/io/ entries remain."""
    # ChromaDB spot check.
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_collection(COLLECTION_NAME)
    batch_size = 5000
    offset = 0
    remaining = 0
    while True:
        results = collection.get(limit=batch_size, offset=offset, include=["metadatas"])
        ids = results["ids"]
        if not ids:
            break
        for m in results["metadatas"]:
            if isinstance(m, dict) and OLD_PREFIX in m.get("source", ""):
                remaining += 1
        offset += len(ids)
        if len(ids) < batch_size:
            break
    if remaining:
        print(
            f"  VERIFY FAIL: {remaining} /home/io/ chunks still in ChromaDB",
            file=sys.stderr,
        )
    else:
        print("  VERIFY OK: ChromaDB — 0 /home/io/ chunks remain")

    # SQLite spot check.
    conn = sqlite3.connect(SQLITE_PATH)
    try:
        for table, col in [
            ("indexed_sources", "source"),
            ("articles", "source_path"),
            ("properties", "source"),
        ]:
            (count,) = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE ?",
                (f"%{OLD_PREFIX}%",),
            ).fetchone()
            if count:
                print(
                    f"  VERIFY FAIL: {count} /home/io/ rows remain in {table}.{col}",
                    file=sys.stderr,
                )
            else:
                print(f"  VERIFY OK: {table}.{col} — 0 /home/io/ rows remain")
    finally:
        conn.close()


def main() -> None:
    print("=== RAG path migration: /home/io/ → /mnt/torus/ ===\n")

    print("[1/3] Migrating ChromaDB...")
    chroma_updated = migrate_chromadb()

    print("\n[2/3] Migrating SQLite rag_metadata.db...")
    sqlite_results = migrate_sqlite()

    total_sqlite = sum(sqlite_results.values())
    print(f"\n  SQLite total rows updated: {total_sqlite}")

    print("\n[3/3] Verifying...")
    verify(set())

    print("\n=== Migration complete ===")
    print(f"  ChromaDB chunks updated : {chroma_updated}")
    for table, count in sqlite_results.items():
        if count:
            print(f"  {table:<35}: {count} rows")


if __name__ == "__main__":
    main()
