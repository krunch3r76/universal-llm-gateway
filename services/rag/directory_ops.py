"""Shared directory-level source discovery and cleanup helpers for RAG.

This module centralizes directory indexing, stale-source detection, and
filesystem-truth cleanup so startup reconciliation and admin routes apply the
same source-level deletion semantics across Chroma and SQLite metadata.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import chromadb

from services.rag.models import IndexResult

logger = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class DirectoryIndexTotals:
    indexed: int = 0
    deleted: int = 0
    unchanged: int = 0
    duplicates: int = 0
    files: int = 0


class IndexFileFn(Protocol):
    """Callable protocol for indexing a single file.

    `force=True` bypasses the hash-unchanged check so metadata updates and
    re-extraction are applied even when the file content has not changed.
    """

    def __call__(
        self,
        path: Path,
        metadata_overrides: dict[str, str | int | float | bool] | None = ...,
        *,
        force: bool = ...,
    ) -> Awaitable[IndexResult]: ...


OnIndexErrorFn = Callable[[Path, Exception], None]
RemoveSourceMetadataFn = Callable[[str, list[str] | None], Awaitable[None]]
ListKnownSourcesFn = Callable[[list[str]], set[str]]


def collect_directory_candidates(
    *,
    dir_path: Path,
    extensions: set[str],
    collect_walked_sources: bool,
) -> tuple[list[Path], set[str]]:
    """Return candidate files and optionally the full walked source set.

    The admin route needs the candidate count before dispatch so it can emit the
    directory-started event, while stale-source cleanup needs the complete walked
    set after dispatch. Keep both concerns on the same traversal so reindex of an
    unchanged tree does not pay for a second recursive walk.
    """
    file_paths: list[Path] = []
    walked_sources: set[str] = set()
    for root, _dirs, files in dir_path.walk():
        for name in files:
            file_path = root / name
            if file_path.suffix.lower() not in extensions:
                continue
            if collect_walked_sources:
                walked_sources.add(str(file_path.resolve()))
            file_paths.append(file_path)
    return file_paths, walked_sources


async def index_directory_contents(
    *,
    file_paths: list[Path],
    index_file: IndexFileFn,
    metadata_overrides: dict[str, str | int | float | bool] | None,
    on_index_error: OnIndexErrorFn,
    force: bool = False,
) -> DirectoryIndexTotals:
    totals = DirectoryIndexTotals()

    if not file_paths:
        return totals

    # Stargate's capacity gate (32 slots) provides natural back-pressure — no semaphore needed.
    # ∀ file_path: per-source asyncio.Lock in _index_file prevents double-indexing.
    async def _process(fp: Path) -> IndexResult | None:
        try:
            return await index_file(fp, metadata_overrides, force=force)
        except Exception as exc:
            on_index_error(fp, exc)
            return None

    results = await asyncio.gather(*(_process(fp) for fp in file_paths))

    for result in results:
        if result is None:
            continue
        totals.indexed += result.indexed
        totals.deleted += result.deleted
        if result.duplicate:
            totals.duplicates += 1
        elif result.unchanged:
            totals.unchanged += 1
        totals.files += 1

    return totals


def find_sources_under_prefixes(
    *,
    collection: chromadb.Collection,
    prefixes: list[str],
    list_known_sources_fn: ListKnownSourcesFn | None = None,
) -> set[str]:
    """Return source paths under the given prefixes from Chroma and metadata-only tables."""
    if not prefixes:
        return set()

    all_data = collection.get(include=["metadatas"])
    rows: list[dict[str, object]] = all_data.get("metadatas") or []
    sources = {
        source
        for row in rows
        if isinstance(row, dict)
        for source in [row.get("source")]
        if isinstance(source, str)
        and any(source.startswith(prefix) for prefix in prefixes)
    }
    if list_known_sources_fn is not None:
        sources.update(list_known_sources_fn(prefixes))
    return sources


def find_removed_directory_sources(
    *,
    collection: chromadb.Collection,
    dir_path: Path,
    walked_sources: set[str],
    list_known_sources_fn: ListKnownSourcesFn | None = None,
) -> set[str]:
    """Return missing source paths under dir_path after a directory reindex walk."""
    dir_prefix = f"{dir_path.resolve()}/"
    known_sources = find_sources_under_prefixes(
        collection=collection,
        prefixes=[dir_prefix],
        list_known_sources_fn=list_known_sources_fn,
    )
    return {
        source
        for source in known_sources
        if source not in walked_sources and not Path(source).exists()
    }


async def delete_sources(
    *,
    collection: chromadb.Collection,
    sources: set[str],
    remove_source_metadata_fn: RemoveSourceMetadataFn | None = None,
) -> tuple[int, int]:
    """Delete a set of sources consistently across Chroma and SQLite metadata."""
    deleted_sources = 0
    deleted_chunks = 0

    for source in sorted(sources):
        existing = collection.get(where={"source": source}, include=[])
        chunk_ids: list[str] = existing.get("ids", [])
        try:
            if chunk_ids:
                collection.delete(ids=chunk_ids)
                deleted_chunks += len(chunk_ids)
            if remove_source_metadata_fn is not None:
                await remove_source_metadata_fn(source, chunk_ids or None)
            deleted_sources += 1
        except Exception as e:
            logger.error(
                "Source deletion failed: source=%s chunk_ids=%d",
                source,
                len(chunk_ids),
                exc_info=True,
            )
            raise e

    return deleted_sources, deleted_chunks


async def purge_orphaned_sources(
    *,
    collection: chromadb.Collection,
    watch_prefixes: list[str],
    remove_source_metadata_fn: RemoveSourceMetadataFn | None = None,
    list_known_sources_fn: ListKnownSourcesFn | None = None,
) -> tuple[int, int, set[str]]:
    """Delete missing watched sources from Chroma and metadata-bearing storage.

    Returns:
        (files_purged, chunks_purged, purged_sources)
    """
    known_sources = find_sources_under_prefixes(
        collection=collection,
        prefixes=watch_prefixes,
        list_known_sources_fn=list_known_sources_fn,
    )
    missing_sources = {source for source in known_sources if not Path(source).exists()}
    if not missing_sources:
        return 0, 0, set()
    files, chunks = await delete_sources(
        collection=collection,
        sources=missing_sources,
        remove_source_metadata_fn=remove_source_metadata_fn,
    )
    return files, chunks, missing_sources
