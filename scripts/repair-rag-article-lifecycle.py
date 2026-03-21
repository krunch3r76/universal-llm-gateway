#!/usr/bin/env python3
"""Repair RAG article/indexed_sources drift using indexed_sources as source-level truth.

Backfills missing article rows for already-indexed sources (Phase 2 structural-sync
rules). Optionally prunes article-only rows whose files no longer exist. Default
is dry-run; pass --apply to write.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.rag.config import RagConfig, load_config  # noqa: E402
from services.rag.property_index import PropertyIndex  # noqa: E402

_DEFAULT_DB_PATH = Path.home() / ".rag" / "store" / "rag_metadata.db"


def _derive_subdirectory(source: str, config: RagConfig) -> str:
    """Return the relative path of the source's directory from its configured watch root.
    For example, if source is `/watch/root/a/b/file.txt`, returns `a/b`.
    """
    source_path = Path(source).expanduser().resolve()
    for watch_directory in config.watch_directories:
        watch_root = Path(watch_directory.path).expanduser().resolve()
        try:
            relative = source_path.relative_to(watch_root)
        except ValueError:
            continue
        parent_parts = relative.parts[:-1]
        return str(Path(*parent_parts)) if parent_parts else ""
    return ""


def _is_under_watch_roots(source: str, config: RagConfig) -> bool:
    """Check if the given source path is located under any of the configured watch roots.

    Args:
        source: The path to the source file.
        config: The RAG configuration object.

    Returns:
        True if the source is under a watch root, False otherwise.
    """
    source_path = Path(source).expanduser().resolve()
    for watch_directory in config.watch_directories:
        watch_root = Path(watch_directory.path).expanduser().resolve()
        try:
            source_path.relative_to(watch_root)
            return True
        except ValueError:
            continue
    return False


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair RAG article/index drift using indexed_sources as the source-level truth"
    )
    parser.add_argument("--apply", action="store_true", help="Write changes")
    parser.add_argument(
        "--prune-missing",
        action="store_true",
        help="Also delete article-only rows whose source_path no longer exists on disk",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    prune_missing_effective = args.prune_missing and args.apply
    if dry_run and args.prune_missing:
        print("--prune-missing has no effect without --apply", file=sys.stderr)

    try:
        config = load_config()
    except ValueError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    return asyncio.run(_run_repair(config, dry_run, prune_missing_effective))


async def _run_repair(
    config: RagConfig,
    dry_run: bool,
    prune_missing: bool,
) -> int:
    """Executes the RAG article/indexed_sources repair logic.

    This function identifies and addresses discrepancies between `indexed_sources`
    and `articles` tables in the RAG metadata database.

    Args:
        config: The RAG configuration.
        dry_run: If True, no changes are written to the database.
        prune_missing: If True, article-only rows whose files no longer exist on disk
                       will be deleted (only if not dry_run).

    Returns:
        An exit code (0 for success, non-zero for errors).
    """
    repaired = 0
    already_present = 0
    missing_on_disk = 0
    outside_watch_roots = 0
    errors = 0

    article_only_missing_on_disk: list[str] = []
    article_only_metadata_existing: list[str] = []
    pruned = 0

    idx = PropertyIndex(_DEFAULT_DB_PATH)
    await idx.start()
    try:
        # Assuming _ensure_conn is intended for internal use, but directly accessed here.
        # If a public method exists to get the connection, it should be used instead.
        conn = idx._ensure_conn()

        # Task 1: indexed_sources without articles
        cursor = conn.execute(
            "SELECT i.source FROM indexed_sources i "
            "LEFT JOIN articles a ON i.source = a.source_path "
            "WHERE a.source_path IS NULL "
            "ORDER BY i.updated_at DESC, i.source ASC"
        )
        sources_without_article = [row[0] for row in cursor.fetchall()]

        for source in sources_without_article:
            path = Path(source).expanduser().resolve()
            if not path.exists():
                missing_on_disk += 1
                continue
            if not _is_under_watch_roots(source, config):
                outside_watch_roots += 1
                continue
            try:
                raw = path.read_bytes()
                content_hash = hashlib.sha256(raw).hexdigest()
                scope = config.get_scope_for_path(source)
                subdirectory = _derive_subdirectory(source, config)
                filename = path.name
            except OSError as e:
                print(f"Error reading {source}: {e}", file=sys.stderr)
                errors += 1
                continue  # Continue to the next source immediately after an error

            if dry_run:
                repaired += 1
                continue

            created = await idx.sync_article_structural_fields(
                source_path=source,
                filename=filename,
                content_hash=content_hash,
                scope=scope,
                subdirectory=subdirectory,
            )
            if created:
                repaired += 1
            else:
                already_present += 1

        # Task 2: article-only rows (articles without indexed_sources)
        cursor = conn.execute(
            "SELECT a.source_path, a.scope, a.subdirectory "
            "FROM articles a "
            "LEFT JOIN indexed_sources i ON a.source_path = i.source "
            "WHERE i.source IS NULL "
            "ORDER BY a.updated_at DESC, a.source_path ASC"
        )
        article_only_rows = cursor.fetchall()

        for source_path, _scope, _subdirectory in article_only_rows:
            if not Path(source_path).expanduser().resolve().exists():
                article_only_missing_on_disk.append(source_path)
            else:
                article_only_metadata_existing.append(source_path)

        if prune_missing and article_only_missing_on_disk and not dry_run:
            for source_path in article_only_missing_on_disk:
                await idx.remove_article(source_path)
                pruned += 1
    finally:
        await idx.stop()

    # Summary
    print("Repair summary:")
    print(f"  repaired: {repaired}")
    print(f"  already_present: {already_present}")
    print(f"  missing_on_disk: {missing_on_disk}")
    print(f"  outside_watch_roots: {outside_watch_roots}")
    print(f"  errors: {errors}")
    print(f"  article_only_missing_on_disk: {len(article_only_missing_on_disk)}")
    print(
        f"  article_only_metadata_existing_file: {len(article_only_metadata_existing)}"
    )
    if pruned:
        print(f"  pruned: {pruned}")
    if article_only_metadata_existing:
        for p in article_only_metadata_existing:
            print(f"    (report only, not deleted) {p}")
    if dry_run and (repaired > 0 or article_only_missing_on_disk):
        print("Dry-run: no changes written. Use --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
