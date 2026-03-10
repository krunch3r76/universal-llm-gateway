"""Admin/CRUD routes for the RAG service.

Extracted from rag_service.py to keep that module under the SLOC limit.
Handles index, reindex, source, stats, watch status, and clear endpoints.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    import chromadb

    from services.rag.property_index import PropertyIndex
    from services.rag.watcher_manager import WatcherManager

from services.rag.directory_ops import (
    IndexFileFn,
    find_removed_sources,
    index_directory_contents,
)
from services.rag.models import (
    ClearResponse,
    IndexDirectoryRequest,
    IndexDirectoryResponse,
    IndexRequest,
    IndexResult,
    SourceResponse,
    StatsResponse,
)

logger = logging.getLogger(__name__)

DEFAULT_EXTENSIONS = [".md", ".mdc", ".txt", ".pdf", ".epub", ".py", ".js", ".ts"]

router = APIRouter()


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


def register_admin_routes(
    *,
    index_file_fn: IndexFileFn,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_watcher_manager_fn: Callable[[], WatcherManager | None],
    get_chroma_fn: Callable[[], chromadb.PersistentClient | None],
    set_collection_fn: Callable[[chromadb.Collection], None],
    collection_name: str,
    get_property_index_fn: Callable[[], PropertyIndex | None],
) -> APIRouter:
    """Register admin routes with the shared service state via closures."""

    @router.post("/index", response_model=IndexResult)
    async def index_file(request: IndexRequest) -> IndexResult:
        return await index_file_fn(
            _validate_file(request.path),
            request.metadata_overrides,
            force=request.force,
        )

    @router.post("/index_directory", response_model=IndexDirectoryResponse)
    async def index_directory(
        request: IndexDirectoryRequest,
    ) -> IndexDirectoryResponse:
        dir_path = _validate_directory(request.path)
        extensions = set(request.extensions or DEFAULT_EXTENSIONS)
        totals, _walked = await index_directory_contents(
            dir_path=dir_path,
            extensions=extensions,
            index_file=index_file_fn,
            metadata_overrides=request.metadata_overrides,
            collect_walked_sources=False,
            on_index_error=lambda fp, exc: logger.warning("Skipping %s: %s", fp, exc),
        )
        return IndexDirectoryResponse(
            indexed=totals.indexed,
            deleted=totals.deleted,
            unchanged=totals.unchanged,
            files=totals.files,
            duplicates=totals.duplicates,
        )

    @router.post("/reindex", response_model=IndexResult)
    async def reindex_file(request: IndexRequest) -> IndexResult:
        return await index_file_fn(
            _validate_file(request.path),
            request.metadata_overrides,
            force=request.force,
        )

    @router.post("/reindex_directory", response_model=IndexDirectoryResponse)
    async def reindex_directory(
        request: IndexDirectoryRequest,
    ) -> IndexDirectoryResponse:
        dir_path = _validate_directory(request.path)
        extensions = set(request.extensions or DEFAULT_EXTENSIONS)
        totals, walked_sources = await index_directory_contents(
            dir_path=dir_path,
            extensions=extensions,
            index_file=index_file_fn,
            metadata_overrides=request.metadata_overrides,
            collect_walked_sources=True,
            on_index_error=lambda fp, exc: logger.warning("Skipping %s: %s", fp, exc),
        )
        collection = get_collection_fn()
        removed_sources = find_removed_sources(
            collection=collection, dir_path=dir_path, walked_sources=walked_sources
        )
        for source in removed_sources:
            stale = collection.get(where={"source": source}, include=[])
            stale_ids: list[str] = stale.get("ids", [])
            if stale_ids:
                collection.delete(ids=stale_ids)
                totals.deleted += len(stale_ids)
                logger.info(
                    "Removed stale chunks: source=%s deleted=%d",
                    source,
                    len(stale_ids),
                )
        return IndexDirectoryResponse(
            indexed=totals.indexed,
            deleted=totals.deleted,
            unchanged=totals.unchanged,
            files=totals.files,
            duplicates=totals.duplicates,
        )

    @router.get("/source", response_model=SourceResponse)
    def get_source(path: str) -> SourceResponse:
        collection = get_collection_fn()
        results = collection.get(
            where={"source": path},
            include=["documents", "metadatas"],
        )
        if not results["documents"]:
            raise HTTPException(
                status_code=404, detail=f"No chunks indexed for: {path}"
            )
        documents = results.get("documents") or []
        metadatas_list = results.get("metadatas") or []
        pairs = sorted(
            zip(documents, metadatas_list, strict=True),
            key=lambda pair: pair[1].get("chunk_index", 0),
        )
        return SourceResponse(
            chunks=[p[0] for p in pairs], metadata=[p[1] for p in pairs]
        )

    @router.get("/stats", response_model=StatsResponse)
    def stats() -> StatsResponse:
        collection = get_collection_fn()
        return StatsResponse(count=collection.count(), collection=collection_name)

    @router.get("/watch/status")
    def watch_status() -> list[dict[str, str | int | bool]]:
        wm = get_watcher_manager_fn()
        if wm is None:
            return []
        return wm.get_status()

    @router.post("/clear", response_model=ClearResponse)
    async def clear() -> ClearResponse:
        chroma = get_chroma_fn()
        assert chroma is not None, "ChromaDB client not initialized"
        collection = get_collection_fn()
        deleted = collection.count()
        chroma.delete_collection(collection_name)
        new_collection = chroma.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        set_collection_fn(new_collection)
        prop_idx = get_property_index_fn()
        if prop_idx is not None:
            await prop_idx.clear()
            logger.info("Property index cleared alongside ChromaDB collection")
        return ClearResponse(deleted=deleted, collection=collection_name)

    return router
