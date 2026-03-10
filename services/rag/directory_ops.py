from __future__ import annotations

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


async def index_directory_contents(
    *,
    dir_path: Path,
    extensions: set[str],
    index_file: IndexFileFn,
    metadata_overrides: dict[str, str | int | float | bool] | None,
    collect_walked_sources: bool,
    on_index_error: OnIndexErrorFn,
    force: bool = False,
) -> tuple[DirectoryIndexTotals, set[str]]:
    totals = DirectoryIndexTotals()
    walked_sources: set[str] = set()

    for root, _dirs, files in dir_path.walk():
        for name in files:
            file_path = root / name
            if file_path.suffix.lower() not in extensions:
                continue
            if collect_walked_sources:
                walked_sources.add(str(file_path.resolve()))
            try:
                result = await index_file(file_path, metadata_overrides, force=force)
            except Exception as exc:
                on_index_error(file_path, exc)
                continue
            totals.indexed += result.indexed
            totals.deleted += result.deleted
            if result.duplicate:
                totals.duplicates += 1
            elif result.unchanged:
                totals.unchanged += 1
            totals.files += 1

    return totals, walked_sources


def find_removed_sources(
    *,
    collection: chromadb.Collection,
    dir_path: Path,
    walked_sources: set[str],
) -> set[str]:
    all_meta = collection.get(include=["metadatas"])
    metadata_rows = all_meta.get("metadatas") or []
    dir_prefix = f"{dir_path.resolve()}/"
    return {
        str(source)
        for row in metadata_rows
        if isinstance(row, dict)
        for source in [row.get("source")]
        if isinstance(source, str)
        and source.startswith(dir_prefix)
        and source not in walked_sources
        and not Path(source).exists()
    }


# Callable type for property-index chunk removal (avoids circular import with PropertyIndex).
RemoveChunkFn = Callable[[str], Awaitable[None]]


async def purge_orphaned_chunks(
    *,
    collection: chromadb.Collection,
    watch_prefixes: list[str],
    remove_chunk_fn: RemoveChunkFn | None = None,
) -> tuple[int, int]:
    """Delete chunks for source files that no longer exist on disk.

    Only sources under watched directory prefixes are examined — externally
    indexed sources are left untouched.

    ∀ source ∈ ChromaDB ∩ watched_prefixes: ¬Path(source).exists() ⟹ delete.

    Returns (files_purged, chunks_purged).
    """
    if not watch_prefixes:
        return 0, 0

    all_data = collection.get(include=["metadatas"])
    rows: list[dict[str, object]] = all_data.get("metadatas") or []
    all_ids: list[str] = all_data.get("ids") or []

    source_to_ids: dict[str, list[str]] = {}
    for chunk_id, row in zip(all_ids, rows, strict=True):
        if not isinstance(row, dict):
            continue
        source = row.get("source")
        if not isinstance(source, str):
            continue
        if not any(source.startswith(prefix) for prefix in watch_prefixes):
            continue
        source_to_ids.setdefault(source, []).append(chunk_id)

    files_purged = 0
    chunks_purged = 0
    for source, ids in source_to_ids.items():
        if not Path(source).exists():
            collection.delete(ids=ids)
            if remove_chunk_fn is not None:
                for chunk_id in ids:
                    await remove_chunk_fn(chunk_id)
            logger.info("Startup orphan purge: source=%s deleted=%d", source, len(ids))
            files_purged += 1
            chunks_purged += len(ids)

    return files_purged, chunks_purged
