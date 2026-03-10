"""Admin/CRUD routes for the RAG service.

Extracted from rag_service.py to keep that module under the SLOC limit.
Handles index, reindex, source, stats, watch status, and clear endpoints.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
from services.rag.events import (
    rag_directory_index_completed,
    rag_directory_index_started,
)
from services.rag.models import (
    ClearResponse,
    ExtractionExportItem,
    ExtractionExportResponse,
    IndexDirectoryRequest,
    IndexDirectoryResponse,
    IndexRequest,
    IndexResult,
    SourceResponse,
    SourcesResponse,
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


async def _bulk_premark(
    dir_path: Path,
    extensions: set[str],
    get_property_index_fn: Callable[[], PropertyIndex | None],
) -> list[Path]:
    """Collect file paths and pre-mark all as pending before concurrent dispatch.

    ∀ fp ∈ returned list: pending journal entry exists before asyncio.gather starts.
    ∀ fp: _index_file_impl calls clear_pending on any exit (success/skip/error).
    Duplicate PDF / unchanged files: clear_pending is called when _index_file_impl
    returns (mark_pending now precedes those early-return paths per Task 5).
    """
    prop_idx = get_property_index_fn()
    seen: set[Path] = set()
    for ext in extensions:
        for fp in dir_path.rglob(f"*{ext}"):
            if fp.is_file() and fp not in seen:
                seen.add(fp)
                if prop_idx is not None:
                    await prop_idx.mark_pending(str(fp))
    return sorted(seen)


def register_admin_routes(
    *,
    index_file_fn: IndexFileFn,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_watcher_manager_fn: Callable[[], WatcherManager | None],
    get_chroma_fn: Callable[[], chromadb.PersistentClient | None],
    set_collection_fn: Callable[[chromadb.Collection], None],
    collection_name: str,
    get_property_index_fn: Callable[[], PropertyIndex | None],
    get_event_bus_fn: Callable[[], object | None] | None = None,
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
        errors: list[Path] = []

        def _on_error(fp: Path, exc: Exception) -> None:
            errors.append(fp)
            logger.warning("Skipping %s: %s", fp, exc)

        file_paths = await _bulk_premark(dir_path, extensions, get_property_index_fn)
        candidate_count = len(file_paths)
        logger.warning(
            "Directory index starting: path=%s files=%d force=%s",
            dir_path,
            candidate_count,
            request.force,
        )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_async_nowait(
                rag_directory_index_started(
                    path=str(dir_path), total_files=candidate_count
                )
            )

        totals, _walked = await index_directory_contents(
            dir_path=dir_path,
            extensions=extensions,
            index_file=index_file_fn,
            metadata_overrides=request.metadata_overrides,
            collect_walked_sources=False,
            on_index_error=_on_error,
            force=request.force,
        )

        if eb:
            await eb.publish_async_nowait(
                rag_directory_index_completed(
                    path=str(dir_path),
                    total_files=candidate_count,
                    indexed=totals.indexed,
                    deleted=totals.deleted,
                    unchanged=totals.unchanged,
                    duplicates=totals.duplicates,
                    errors=len(errors),
                )
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
        errors: list[Path] = []

        def _on_error(fp: Path, exc: Exception) -> None:
            errors.append(fp)
            logger.warning("Skipping %s: %s", fp, exc)

        file_paths = await _bulk_premark(dir_path, extensions, get_property_index_fn)
        candidate_count = len(file_paths)
        logger.warning(
            "Directory reindex starting: path=%s files=%d force=%s",
            dir_path,
            candidate_count,
            request.force,
        )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_async_nowait(
                rag_directory_index_started(
                    path=str(dir_path), total_files=candidate_count
                )
            )

        totals, walked_sources = await index_directory_contents(
            dir_path=dir_path,
            extensions=extensions,
            index_file=index_file_fn,
            metadata_overrides=request.metadata_overrides,
            collect_walked_sources=True,
            on_index_error=_on_error,
            force=request.force,
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

        if eb:
            await eb.publish_async_nowait(
                rag_directory_index_completed(
                    path=str(dir_path),
                    total_files=candidate_count,
                    indexed=totals.indexed,
                    deleted=totals.deleted,
                    unchanged=totals.unchanged,
                    duplicates=totals.duplicates,
                    errors=len(errors),
                )
            )
        return IndexDirectoryResponse(
            indexed=totals.indexed,
            deleted=totals.deleted,
            unchanged=totals.unchanged,
            files=totals.files,
            duplicates=totals.duplicates,
        )

    @router.get("/extraction_export", response_model=ExtractionExportResponse)
    def extraction_export(
        prefix: str | None = None, include_text: bool = False
    ) -> ExtractionExportResponse:
        """Bulk export chunk extractions, optionally filtered by source prefix.

        Queries ChromaDB directly so chunks without any extraction (missing field)
        are also included — unlike /sources which is property-index-only.
        Set include_text=true to include the chunk document text in the response.
        """
        collection = get_collection_fn()
        include = ["metadatas"] if not include_text else ["documents", "metadatas"]
        results = collection.get(include=include)
        ids: list[str] = results.get("ids") or []
        raw_docs = results.get("documents")
        raw_metas = results.get("metadatas")
        n = len(ids)
        docs: list[str] = (
            raw_docs
            if isinstance(raw_docs, list) and len(raw_docs) == n
            else ["" for _ in ids]
        )
        metas: list[dict[str, Any]] = (
            raw_metas
            if isinstance(raw_metas, list) and len(raw_metas) == n
            else [{} for _ in ids]
        )

        items: list[ExtractionExportItem] = []
        sources_seen: set[str] = set()
        for chunk_id, text, meta in zip(ids, docs, metas, strict=True):
            if not isinstance(meta, dict):
                continue
            source = meta.get("source") or ""
            if not source:
                continue
            if prefix and not source.startswith(prefix):
                continue
            sources_seen.add(source)
            ext = meta.get("extraction")
            items.append(
                ExtractionExportItem(
                    source=source,
                    chunk_id=chunk_id,
                    chunk_index=int(meta.get("chunk_index", 0)),
                    text=text or "",
                    extraction=ext if isinstance(ext, str) else None,
                    extraction_model=meta.get("extraction_model"),
                    extraction_schema_version=(
                        str(v) if (v := meta.get("extraction_schema_version")) else None
                    ),
                )
            )
        items.sort(key=lambda x: (x.source, x.chunk_index))
        return ExtractionExportResponse(
            total_chunks=len(items),
            total_sources=len(sources_seen),
            items=items,
        )

    @router.get("/sources", response_model=SourcesResponse)
    def get_sources(prefix: str | None = None) -> SourcesResponse:
        """List distinct source paths in the property index, optionally filtered by prefix."""
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            return SourcesResponse(sources=[])
        return SourcesResponse(sources=prop_idx.get_sources(prefix=prefix))

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
