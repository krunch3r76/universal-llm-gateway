from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import chromadb

from services.rag.models import IndexResult


@dataclass(slots=True, kw_only=True)
class DirectoryIndexTotals:
    indexed: int = 0
    deleted: int = 0
    unchanged: int = 0
    duplicates: int = 0
    files: int = 0


IndexFileFn = Callable[
    [Path, dict[str, str | int | float | bool] | None],
    Awaitable[IndexResult],
]
OnIndexErrorFn = Callable[[Path, Exception], None]


async def index_directory_contents(
    *,
    dir_path: Path,
    extensions: set[str],
    index_file: IndexFileFn,
    metadata_overrides: dict[str, str | int | float | bool] | None,
    collect_walked_sources: bool,
    on_index_error: OnIndexErrorFn,
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
                result = await index_file(file_path, metadata_overrides)
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
