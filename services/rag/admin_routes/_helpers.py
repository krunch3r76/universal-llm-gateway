"""Shared helpers and validators for admin routes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from collections.abc import Callable

    import chromadb

    from services.rag.property_index import PropertyIndex

from typing import TypedDict

from services.rag.config import BASELINE_EXTENSIONS
from services.rag.directory_ops import (
    delete_sources,
    find_sources_under_prefixes,
)

logger = logging.getLogger(__name__)


class OrphanedArticle(TypedDict):
    source_path: str
    title: str
    scope: str
    updated_at: str


class OrphanedArticlesResponse(TypedDict):
    orphans: list[OrphanedArticle]
    count: int


DEFAULT_EXTENSIONS = list(BASELINE_EXTENSIONS)

type ArticleRow = dict[str, str]


def _coverage_sources(
    *,
    prop_idx: PropertyIndex | None,
    chroma_sources: set[str],
) -> list[str]:
    """Return source paths visible to coverage for both extracted and raw-only corpora.

    Property-backed scopes surface via ``properties.source``. Extraction-disabled
    corpora such as ``persian_poetry`` can legitimately have zero property rows
    while still being fully indexed in ChromaDB and cached in ``indexed_sources``.
    Coverage must union all three surfaces instead of treating properties as the
    sole authority.
    """
    if prop_idx is None:
        return sorted(chroma_sources)
    property_sources = set(prop_idx.get_sources())
    cached_sources = set(prop_idx.get_indexed_sources())
    return sorted(property_sources | cached_sources | chroma_sources)


def _align_list_length(
    values: list[Any] | None,
    expected: int,
    default_factory: Callable[[], Any],
) -> list[Any]:
    """Return a list exactly `expected` long by trimming or padding defaults."""
    padded = list(values or [])
    if len(padded) >= expected:
        return padded[:expected]
    num_to_add = expected - len(padded)
    if num_to_add > 0:
        padded.extend(default_factory() for _ in range(num_to_add))
    return padded


def _validate_file(path: str) -> Path:
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    return file_path


def _validate_directory(path: str) -> Path:
    dir_path = Path(path)
    if not dir_path.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")
    if not dir_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return dir_path


async def _clear_directory_sources(
    dir_path: Path,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_property_index_fn: Callable[[], PropertyIndex | None],
) -> tuple[int, int]:
    """Delete all known sources under dir_path from ChromaDB and SQLite metadata."""
    collection = get_collection_fn()
    dir_prefix = f"{dir_path.resolve()}/"
    prop_idx = get_property_index_fn()
    sources = find_sources_under_prefixes(
        collection=collection,
        prefixes=[dir_prefix],
        list_known_sources_fn=prop_idx.list_known_sources if prop_idx else None,
    )
    if not sources:
        return 0, 0
    remove_fn = prop_idx.remove_source_metadata if prop_idx else None
    return await delete_sources(
        collection=collection,
        sources=sources,
        remove_source_metadata_fn=remove_fn,
    )
