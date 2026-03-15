"""Admin/CRUD routes for the RAG service.

Extracted during the rag_service module split to keep files under SLOC limits.
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

from services.rag.config import BASELINE_EXTENSIONS
from services.rag.directory_ops import (
    IndexFileFn,
    delete_sources,
    find_removed_directory_sources,
    find_sources_under_prefixes,
    index_directory_contents,
)
from services.rag.events.articles import rag_article_upserted
from services.rag.events.indexing import (
    rag_directory_cleared,
    rag_directory_index_completed,
    rag_directory_index_started,
)
from services.rag.models import (
    ArticleUpsertRequest,
    ArticleUpsertResponse,
    ClearDirectoryRequest,
    ClearDirectoryResponse,
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

DEFAULT_EXTENSIONS = list(BASELINE_EXTENSIONS)

router = APIRouter()


def _align_list_length(
    values: list[Any] | None,
    expected: int,
    default_factory: Callable[[], Any],
) -> list[Any]:
    """Return a list exactly `expected` long by trimming or padding defaults."""
    if not isinstance(values, list):
        return [default_factory() for _ in range(expected)]
    if len(values) >= expected:
        return values[:expected]
    padded = list(values)
    padded.extend(default_factory() for _ in range(expected - len(values)))
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


async def _bulk_premark(
    dir_path: Path,
    extensions: set[str],
    get_property_index_fn: Callable[[], PropertyIndex | None],
) -> list[Path]:
    """Collect file paths and pre-mark all as pending before concurrent dispatch.

    Ensures a pending journal entry exists for each file before indexing runs,
    so _index_file_impl can clear_pending on any exit (success/skip/error).
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


def register_admin_routes(
    *,
    index_file_fn: IndexFileFn,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_watcher_manager_fn: Callable[[], WatcherManager | None],
    get_chroma_fn: Callable[[], chromadb.PersistentClient | None],
    set_collection_fn: Callable[[chromadb.Collection], None],
    collection_name: str,
    get_property_index_fn: Callable[[], PropertyIndex | None],
    get_event_bus_fn: Callable[[], Any | None] | None = None,
) -> APIRouter:
    """Register admin routes with the shared service state via closures."""

    async def _index_single_file(request: IndexRequest) -> IndexResult:
        return await index_file_fn(
            _validate_file(request.path),
            request.metadata_overrides,
            force=request.force,
        )

    async def _run_directory_index(
        dir_path: Path,
        extensions: set[str],
        metadata_overrides: dict[str, str | int | float | bool] | None,
        force: bool,
        *,
        is_reindex: bool,
    ) -> IndexDirectoryResponse:
        """Shared flow for index_directory and reindex_directory.

        When is_reindex=True: upfront clear when force=True, collect walked_sources,
        then delete chunks for sources no longer under dir_path.
        """
        errors: list[Path] = []

        def _on_error(fp: Path, exc: Exception) -> None:
            errors.append(fp)
            logger.warning("Skipping %s: %s", fp, exc)

        eb = get_event_bus_fn() if get_event_bus_fn else None

        if is_reindex and force:
            sources_cleared, chunks_cleared = await _clear_directory_sources(
                dir_path, get_collection_fn, get_property_index_fn
            )
            logger.warning(
                "Force reindex upfront clear: path=%s sources=%d chunks=%d",
                dir_path,
                sources_cleared,
                chunks_cleared,
            )
            if eb:
                await eb.publish_async_nowait(
                    rag_directory_cleared(
                        path=str(dir_path),
                        sources_cleared=sources_cleared,
                        chunks_cleared=chunks_cleared,
                    )
                )

        file_paths = await _bulk_premark(dir_path, extensions, get_property_index_fn)
        candidate_count = len(file_paths)
        logger.warning(
            "Directory %s starting: path=%s files=%d force=%s",
            "reindex" if is_reindex else "index",
            dir_path,
            candidate_count,
            force,
        )
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
            metadata_overrides=metadata_overrides,
            collect_walked_sources=is_reindex,
            on_index_error=_on_error,
            force=force,
        )

        if is_reindex:
            collection = get_collection_fn()
            stale_prop_idx = get_property_index_fn()
            try:
                removed_sources = find_removed_directory_sources(
                    collection=collection,
                    dir_path=dir_path,
                    walked_sources=walked_sources,
                    list_known_sources_fn=(
                        stale_prop_idx.list_known_sources
                        if stale_prop_idx is not None
                        else None
                    ),
                )
                removed_source_count, removed_chunk_count = await delete_sources(
                    collection=collection,
                    sources=removed_sources,
                    remove_source_metadata_fn=(
                        stale_prop_idx.remove_source_metadata
                        if stale_prop_idx is not None
                        else None
                    ),
                )
                totals.deleted += removed_chunk_count
                if removed_source_count:
                    logger.info(
                        "Removed stale directory sources: path=%s sources=%d chunks=%d",
                        dir_path,
                        removed_source_count,
                        removed_chunk_count,
                    )
            except Exception as exc:
                logger.error(
                    "Directory stale-source cleanup failed: path=%s error=%s",
                    dir_path,
                    exc,
                    exc_info=True,
                )
                errors.append(dir_path)

        prop_idx = get_property_index_fn()
        if prop_idx is not None:
            await prop_idx.stamp_watermark("reindex")

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

    @router.post("/index", response_model=IndexResult)
    async def index_file(request: IndexRequest) -> IndexResult:
        return await _index_single_file(request)

    # ...

    @router.post("/reindex", response_model=IndexResult)
    async def reindex_file(request: IndexRequest) -> IndexResult:
        return await _index_single_file(request)

    @router.post("/index_directory", response_model=IndexDirectoryResponse)
    async def index_directory(
        request: IndexDirectoryRequest,
    ) -> IndexDirectoryResponse:
        dir_path = _validate_directory(request.path)
        extensions = set(request.extensions or DEFAULT_EXTENSIONS)
        return await _run_directory_index(
            dir_path,
            extensions,
            request.metadata_overrides,
            request.force,
            is_reindex=False,
        )

    @router.post("/reindex_directory", response_model=IndexDirectoryResponse)
    async def reindex_directory(
        request: IndexDirectoryRequest,
    ) -> IndexDirectoryResponse:
        dir_path = _validate_directory(request.path)
        extensions = set(request.extensions or DEFAULT_EXTENSIONS)
        return await _run_directory_index(
            dir_path,
            extensions,
            request.metadata_overrides,
            request.force,
            is_reindex=True,
        )

    @router.post("/clear_directory", response_model=ClearDirectoryResponse)
    async def clear_directory(
        request: ClearDirectoryRequest,
    ) -> ClearDirectoryResponse:
        """Delete all chunks and property index entries for every source under the given path.

        Use this to clear a directory before a fresh manual re-index, or to remove
        a corpus entirely without touching other scopes.
        """
        dir_path = _validate_directory(request.path)
        sources_cleared, chunks_cleared = await _clear_directory_sources(
            dir_path, get_collection_fn, get_property_index_fn
        )
        logger.warning(
            "Directory cleared: path=%s sources=%d chunks=%d",
            dir_path,
            sources_cleared,
            chunks_cleared,
        )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_async_nowait(
                rag_directory_cleared(
                    path=str(dir_path),
                    sources_cleared=sources_cleared,
                    chunks_cleared=chunks_cleared,
                )
            )
        return ClearDirectoryResponse(
            sources_cleared=sources_cleared,
            chunks_cleared=chunks_cleared,
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
        n = len(ids)
        docs = _align_list_length(results.get("documents"), n, lambda: "")
        metas = _align_list_length(results.get("metadatas"), n, dict)

        items: list[ExtractionExportItem] = []
        sources_seen: set[str] = set()
        for chunk_id, text, meta in zip(ids, docs, metas):
            if not isinstance(meta, dict):
                continue
            source = meta.get("source") or ""
            if not source:
                continue
            if prefix and not source.startswith(prefix):
                continue
            sources_seen.add(source)
            ext = meta.get("extraction")
            em = meta.get("extraction_model")
            items.append(
                ExtractionExportItem(
                    source=source,
                    chunk_id=chunk_id,
                    chunk_index=int(meta.get("chunk_index", 0)),
                    text=text or "",
                    extraction=ext if isinstance(ext, str) else None,
                    extraction_model=em if isinstance(em, str) else None,
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
        documents = _align_list_length(
            results.get("documents"), len(results["documents"]), lambda: ""
        )
        metadatas_list = _align_list_length(
            results.get("metadatas"), len(documents), dict
        )
        pairs = sorted(
            zip(documents, metadatas_list),
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
        if chroma is None:
            logger.error("ChromaDB client not initialized when /clear was called.")
            raise HTTPException(
                status_code=500, detail="ChromaDB client not initialized"
            )
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

    @router.post("/article", response_model=ArticleUpsertResponse)
    async def upsert_article(request: ArticleUpsertRequest) -> ArticleUpsertResponse:
        """Insert or update an article metadata row.

        Non-empty fields overwrite existing values; empty strings preserve
        the current value (merge semantics). The ``content_hash`` field is
        the plain SHA-256 of the source file bytes — the join key that
        connects article metadata to indexed chunks at query time.
        """
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")
        filename = request.filename or Path(request.source_path).name
        created = await prop_idx.upsert_article(
            source_path=request.source_path,
            filename=filename,
            title=request.title,
            authors=request.authors,
            venue=request.venue,
            published_date=request.published_date,
            doi=request.doi,
            abstract=request.abstract,
            content_hash=request.content_hash,
            subdirectory=request.subdirectory,
            scope=request.scope,
        )
        logger.info(
            "Article %s: source_path=%s title=%s",
            "created" if created else "updated",
            request.source_path,
            request.title[:60] if request.title else "(empty)",
        )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_async_nowait(
                rag_article_upserted(
                    source_path=request.source_path,
                    created=created,
                    title=request.title,
                    content_hash=request.content_hash,
                )
            )
        return ArticleUpsertResponse(source_path=request.source_path, created=created)

    return router
