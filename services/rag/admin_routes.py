"""Admin/CRUD routes for the RAG service.

Provides operational endpoints for indexing and reindexing files/directories,
source lifecycle cleanup, extraction exports, and coverage/status reporting.
These routes coordinate ChromaDB data with SQLite-backed metadata surfaces
(property index, failures, articles) to keep retrieval state coherent.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    from collections.abc import Callable

    import chromadb
    from universal_event_bus import EventBus

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex
    from services.rag.watcher_manager import WatcherManager

from services.rag.config import BASELINE_EXTENSIONS
from services.rag.directory_ops import (
    IndexFileFn,
    collect_directory_candidates,
    delete_sources,
    find_removed_directory_sources,
    find_sources_under_prefixes,
    index_directory_contents,
)
from services.rag.events.articles import (
    rag_article_upserted,
    rag_directory_sources_deleted,
    rag_source_deleted,
)
from services.rag.events.indexing import (
    rag_directory_cleared,
    rag_directory_index_completed,
    rag_directory_index_started,
)
from services.rag.models import (
    ArticleListingItem,
    ArticleListingResponse,
    ArticleUpsertRequest,
    ArticleUpsertResponse,
    ClearDirectoryRequest,
    ClearDirectoryResponse,
    ClearResponse,
    CoverageResponse,
    DirectoryDeleteResponse,
    ExtractionExportItem,
    ExtractionExportResponse,
    IndexDirectoryRequest,
    IndexDirectoryResponse,
    IndexingStatusResponse,
    IndexRequest,
    IndexResult,
    PrefixCoverage,
    RefreshCorpusHintsRequest,
    RefreshCorpusHintsResponse,
    ScopeCoverage,
    SourceDeleteResponse,
    SourceResponse,
    SourcesResponse,
    StatsResponse,
    WatcherStatusItem,
)

logger = logging.getLogger(__name__)

DEFAULT_EXTENSIONS = list(BASELINE_EXTENSIONS)

router = APIRouter()
type ArticleRow = dict[str, str]


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


def register_admin_routes(
    *,
    index_file_fn: IndexFileFn,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_watcher_manager_fn: Callable[[], WatcherManager | None],
    get_chroma_fn: Callable[[], chromadb.PersistentClient | None],
    set_collection_fn: Callable[[chromadb.Collection], None],
    collection_name: str,
    get_property_index_fn: Callable[[], PropertyIndex | None],
    get_event_bus_fn: Callable[[], EventBus | None] | None = None,
    get_config_fn: Callable[[], RagConfig | None] | None = None,
    refresh_article_registry_from_row_fn: Callable[[ArticleRow | None], None]
    | None = None,
    reconcile_article_registry_delete_fn: Callable[[str, ArticleRow | None], None]
    | None = None,
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
            # This block should be removed or refactored if _clear_directory_sources emits the event
            # Or, if _clear_directory_sources does not emit, create a helper:
            # await _publish_directory_cleared_event(eb, dir_path, sources_cleared, chunks_cleared)

        file_paths, walked_sources = collect_directory_candidates(
            dir_path=dir_path,
            extensions=extensions,
            collect_walked_sources=is_reindex,
        )
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

        totals = await index_directory_contents(
            file_paths=file_paths,
            index_file=index_file_fn,
            metadata_overrides=metadata_overrides,
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
                # Consider a separate error counter for cleanup issues
                # totals.cleanup_errors += 1

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

    @router.post("/reindex", response_model=IndexResult)
    async def reindex_file(request: IndexRequest) -> IndexResult:
        # Keep /reindex as an explicit alias for operational clarity.
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
        malformed_count = 0
        for chunk_id, text, meta in zip(ids, docs, metas):
            if not isinstance(meta, dict):
                logger.warning(
                    "Skipping chunk_id %s due to malformed metadata in extraction_export",
                    chunk_id,
                )
                malformed_count += 1
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
        if malformed_count:
            logger.warning(
                "extraction_export omitted %d chunks with malformed metadata",
                malformed_count,
            )
        items.sort(key=lambda x: (x.source, x.chunk_index))
        # Add event emission here
        # if eb:
        #     await eb.publish_async_nowait(
        #         rag_extraction_exported(
        #             prefix=prefix,
        #             include_text=include_text,
        #             total_chunks=len(items),
        #             total_sources=len(sources_seen),
        #             malformed_chunks=malformed_count,
        #         )
        #     )
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
        raw_documents = results.get("documents") or []
        if not raw_documents:
            raise HTTPException(
                status_code=404, detail=f"No chunks indexed for: {path}"
            )
        num_documents = len(raw_documents)
        documents = _align_list_length(raw_documents, num_documents, lambda: "")
        metadatas_list = _align_list_length(
            results.get("metadatas"), num_documents, dict
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

    @router.get("/indexing/status", response_model=IndexingStatusResponse)
    def indexing_status(
        sample_limit: int = Query(default=20, ge=0, le=100),
    ) -> IndexingStatusResponse:
        """Return bounded indexing backlog and health from live RAG-owned state."""
        normalized_limit = max(0, min(sample_limit, 100))
        pending_count = 0
        pending_sample: list[str] = []
        pending_sample_truncated = False
        failed_extractions_count = 0
        failed_extractions_permanent_count = 0
        indexed_sources_count = 0
        property_index_available = True
        watchers: list[WatcherStatusItem] = []
        chunks: int | None = None
        collection: str | None = None
        chroma_available = True
        chroma_error: str | None = None

        prop_idx = get_property_index_fn()
        if prop_idx is None:
            property_index_available = False
        else:
            try:
                pending_snapshot = prop_idx.get_pending_snapshot(normalized_limit)
                pending_count = pending_snapshot.count
                pending_sample = pending_snapshot.sample
                pending_sample_truncated = normalized_limit > 0 and pending_count > len(
                    pending_sample
                )
                failure_snapshot = prop_idx.get_failure_snapshot()
                failed_extractions_count = failure_snapshot.failed_extractions_count
                failed_extractions_permanent_count = (
                    failure_snapshot.failed_extractions_permanent_count
                )
                indexed_sources_count = prop_idx.get_indexed_source_count()
            except sqlite3.Error as exc:
                logger.warning(
                    "Indexing status degraded while reading property index: %s",
                    exc,
                    exc_info=True,
                )
                property_index_available = False
            except Exception as exc:
                logger.error(
                    "Unexpected error reading property index for status: %s",
                    exc,
                    exc_info=True,
                )
                property_index_available = False

        wm = get_watcher_manager_fn()
        if wm is not None:
            for row in wm.get_status():
                if not isinstance(row, dict):
                    continue
                watchers.append(
                    WatcherStatusItem(
                        path=str(row.get("path", "")),
                        enabled=bool(row.get("enabled", False)),
                        reload_count=int(row.get("reload_count", 0)),
                        error_count=int(row.get("error_count", 0)),
                    )
                )

        try:
            current_collection = get_collection_fn()
            chunks = int(current_collection.count())
            collection = collection_name
        except Exception as exc:
            chroma_available = False
            chroma_error = str(exc)
            logger.warning(
                "Indexing status degraded while reading ChromaDB count: %s",
                exc,
                exc_info=True,
            )

        return IndexingStatusResponse(
            pending_count=pending_count,
            pending_sample=pending_sample,
            pending_sample_truncated=pending_sample_truncated,
            chunks=chunks,
            collection=collection,
            chroma_available=chroma_available,
            chroma_error=chroma_error,
            watchers=watchers,
            failed_extractions_count=failed_extractions_count,
            failed_extractions_permanent_count=failed_extractions_permanent_count,
            indexed_sources_count=indexed_sources_count,
            property_index_available=property_index_available,
        )

    @router.post("/clear", response_model=ClearResponse)
    async def clear() -> ClearResponse:
        chroma = get_chroma_fn()
        if chroma is None:
            logger.error("ChromaDB client not initialized when /clear was called.")
            raise HTTPException(
                status_code=500, detail="ChromaDB client not initialized"
            )
        collection = get_collection_fn()
        initial_count = collection.count()
        try:
            chroma.delete_collection(collection_name)
            deleted = (
                initial_count  # Assuming successful deletion means all were removed
            )
        except Exception as exc:
            logger.error(
                "Failed to delete ChromaDB collection %s: %s",
                collection_name,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to clear collection: {exc}"
            )
        # Or, if delete_collection returns the count:
        # deleted = chroma.delete_collection(collection_name)
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

    @router.get("/coverage", response_model=CoverageResponse)
    def get_coverage() -> CoverageResponse:
        """Per-scope, per-prefix view of indexed file counts and recency.

        Aggregates property index sources against configured scope prefixes.
        Optionally enriches with last_indexed timestamps from ChromaDB chunk
        metadata (skipped when collection exceeds 100k chunks).
        """
        config = get_config_fn() if get_config_fn else None
        if config is None:
            return CoverageResponse(scopes={})

        prop_idx = get_property_index_fn()
        all_sources = prop_idx.get_sources() if prop_idx else []

        # Build source -> max(indexed_at) from ChromaDB metadata.
        # Skip for very large collections to keep this admin endpoint responsive.
        source_last_indexed: dict[str, str] = {}
        collection = get_collection_fn()
        chunk_count = collection.count()
        max_chunks_for_ts_scan = 100_000
        timestamp_scan_degraded = chunk_count > max_chunks_for_ts_scan
        if chunk_count <= max_chunks_for_ts_scan:
            try:
                raw = collection.get(include=["metadatas"])
                for meta in raw.get("metadatas") or []:
                    if not isinstance(meta, dict):
                        continue
                    src = meta.get("source", "")
                    ts = meta.get("indexed_at", "")
                    if isinstance(src, str) and isinstance(ts, str) and src and ts:
                        existing = source_last_indexed.get(src, "")
                        if ts > existing:
                            source_last_indexed[src] = ts
            except Exception as e:
                logger.warning(
                    "Coverage: ChromaDB timestamp scan failed, omitting last_indexed: %s",
                    e,
                    exc_info=True,
                )
                timestamp_scan_degraded = True
                # Add a field to CoverageResponse to hold this error message
                # self.timestamp_scan_error = str(e)

        scopes: dict[str, ScopeCoverage] = {}
        for scope_name, scope_def in config.scopes.items():
            prefix_coverages: list[PrefixCoverage] = []
            scope_total = 0
            for pfx in scope_def.prefixes:
                normalized = pfx.rstrip("/") + "/"
                matched = [s for s in all_sources if s.startswith(normalized)]
                count = len(matched)
                scope_total += count
                last_ts: str | None = None
                for s in matched:
                    ts = source_last_indexed.get(s)
                    if ts and (last_ts is None or ts > last_ts):
                        last_ts = ts
                prefix_coverages.append(
                    PrefixCoverage(path=pfx, indexed_files=count, last_indexed=last_ts)
                )
            scopes[scope_name] = ScopeCoverage(
                prefixes=prefix_coverages, total_indexed=scope_total
            )

        return CoverageResponse(
            scopes=scopes,
            timestamp_scan_degraded=timestamp_scan_degraded,
        )

    @router.delete("/source", response_model=SourceDeleteResponse)
    async def delete_source(path: str) -> SourceDeleteResponse:
        """Remove a single source from all storage surfaces.

        Deletes ChromaDB chunks, FTS entries, property index entries,
        failed extraction records, and the articles table row.
        """
        prop_idx = get_property_index_fn()
        collection = get_collection_fn()

        existing = collection.get(where={"source": path}, include=[])
        chunk_ids: list[str] = existing.get("ids", [])
        chunks_deleted = len(chunk_ids)

        if chunk_ids:
            collection.delete(ids=chunk_ids)

        properties_removed = 0
        fts_removed = 0
        if prop_idx is not None:
            if chunk_ids:
                properties_removed = await prop_idx.remove_properties_for_chunks(
                    chunk_ids
                )
                fts_removed = await prop_idx.fts.remove_batch(chunk_ids)
            await prop_idx.clear_failures_for(path)
            await prop_idx.remove_indexed_source(path)

        fallback_row = None
        if prop_idx is not None:
            fallback_row = prop_idx.find_latest_article_by_filename(
                Path(path).name,
                exclude_source_path=path,
            )
        article_deleted = False
        if prop_idx is not None:
            article_deleted = await prop_idx.remove_article(path)
        if reconcile_article_registry_delete_fn is not None:
            reconcile_article_registry_delete_fn(
                source_path=path,
                fallback_row=fallback_row,
            )

        logger.info(
            "Source deleted: source=%s chunks=%d properties=%d article=%s",
            path,
            chunks_deleted,
            properties_removed,
            article_deleted,
        )

        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_async_nowait(
                rag_source_deleted(
                    source=path,
                    chunks_deleted=chunks_deleted,
                    article_deleted=article_deleted,
                )
            )

        return SourceDeleteResponse(
            source=path,
            chunks_deleted=chunks_deleted,
            fts_removed=fts_removed,
            properties_removed=properties_removed,
            article_deleted=article_deleted,
        )

    @router.delete("/directory", response_model=DirectoryDeleteResponse)
    async def delete_directory(path: str) -> DirectoryDeleteResponse:
        """Remove all sources under a directory prefix from all storage surfaces.

        Prefix-matches source paths to find every file under the directory,
        then deletes ChromaDB chunks, FTS, properties, and articles for each.
        """
        prop_idx = get_property_index_fn()
        collection = get_collection_fn()
        dir_prefix = path.rstrip("/") + "/"

        sources = find_sources_under_prefixes(
            collection=collection,
            prefixes=[dir_prefix],
            list_known_sources_fn=prop_idx.list_known_sources if prop_idx else None,
        )
        if not sources:
            return DirectoryDeleteResponse(
                path=path,
                sources_deleted=0,
                chunks_deleted=0,
                fts_removed=0,
                articles_deleted=0,
            )

        total_chunks = 0
        total_fts_removed = 0  # Renamed for clarity
        total_properties_removed = 0  # Added for consistency
        total_articles = 0
        for source in sorted(sources):
            existing = collection.get(where={"source": source}, include=[])
            chunk_ids: list[str] = existing.get("ids", [])
            if chunk_ids:
                collection.delete(ids=chunk_ids)
                total_chunks += len(chunk_ids)
            if prop_idx is not None:
                if chunk_ids:
                    total_properties_removed += (
                        await prop_idx.remove_properties_for_chunks(chunk_ids)
                    )
                    total_fts_removed += await prop_idx.fts.remove_batch(chunk_ids)
                await prop_idx.clear_failures_for(source)
                await prop_idx.remove_indexed_source(source)
                if await prop_idx.remove_article(source):
                    total_articles += 1

        logger.info(
            "Directory deleted: path=%s sources=%d chunks=%d fts=%d articles=%d",
            path,
            len(sources),
            total_chunks,
            total_fts_removed,
            total_articles,
        )

        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_async_nowait(
                rag_directory_sources_deleted(
                    path=path,
                    sources_deleted=len(sources),
                    chunks_deleted=total_chunks,
                    articles_deleted=total_articles,
                )
            )

        return DirectoryDeleteResponse(
            path=path,
            sources_deleted=len(sources),
            chunks_deleted=total_chunks,
            fts_removed=total_fts_removed,
            articles_deleted=total_articles,
        )

    class OrphanedArticle(TypedDict):
        source_path: str
        title: str
        scope: str
        updated_at: str

    class OrphanedArticlesResponse(TypedDict):
        orphans: list[OrphanedArticle]
        count: int

    def _parse_scope_filter(scope: str | None) -> list[str]:
        """Parse and normalize comma-separated scope query values."""
        if scope is None:
            return []
        normalized = [token.strip() for token in scope.split(",") if token.strip()]
        return list(dict.fromkeys(normalized))

    @router.get(
        "/articles",
        response_model=ArticleListingResponse,
        response_model_exclude_none=True,
    )
    def list_articles(
        scope: str | None = Query(default=None),
        include_abstract: bool = Query(default=False),
    ) -> ArticleListingResponse:
        """List structured article metadata with optional scope and abstract filters."""
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")

        scopes = _parse_scope_filter(scope)
        select_cols = [
            "source_path",
            "filename",
            "title",
            "authors",
            "venue",
            "published_date",
            "doi",
            "scope",
            "comments",
            "updated_at",
        ]
        if include_abstract:
            select_cols.append("abstract")

        query = f"SELECT {', '.join(select_cols)} FROM articles"
        params: tuple[str, ...] = ()
        if scopes:
            placeholders = ", ".join("?" for _ in scopes)
            query += f" WHERE scope IN ({placeholders})"
            params = tuple(scopes)
        query += (
            " ORDER BY scope ASC, published_date DESC, filename ASC, source_path ASC"
        )

        conn = prop_idx._ensure_conn()
        try:
            rows = conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            logger.error("Article listing query failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500, detail="Failed to query article metadata"
            ) from exc

        articles = [
            ArticleListingItem(
                source_path=row["source_path"] or "",
                filename=row["filename"] or "",
                title=row["title"] or "",
                authors=row["authors"] or "",
                venue=row["venue"] or "",
                published_date=row["published_date"] or "",
                doi=row["doi"] or "",
                scope=row["scope"] or "",
                comments=row["comments"] or "",
                updated_at=row["updated_at"] or "",
                abstract=(row["abstract"] or "") if include_abstract else None,
            )
            for row in rows
        ]
        return ArticleListingResponse(
            articles=articles,
            count=len(articles),
            scopes_queried=scopes,
        )

    @router.get(
        "/orphaned_articles",
        response_model=OrphanedArticlesResponse,
    )
    def get_orphaned_articles() -> OrphanedArticlesResponse:
        """Return articles that have no corresponding indexed chunks.

        An article is "orphaned" when rag_upsert_article was called but
        the file was never indexed (or its chunks were deleted). These
        rows accumulate silently and should be cleaned up periodically.
        """
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            return {"orphans": [], "count": 0}
        conn = prop_idx._ensure_conn()
        rows = conn.execute(
            "SELECT a.source_path, a.title, a.scope, a.updated_at "
            "FROM articles a "
            "LEFT JOIN indexed_sources s ON a.source_path = s.source "
            "WHERE s.source IS NULL "
            "ORDER BY a.updated_at DESC"
        ).fetchall()
        return {
            "orphans": [
                {
                    "source_path": r[0],
                    "title": r[1],
                    "scope": r[2],
                    "updated_at": r[3],
                }
                for r in rows
            ],
            "count": len(rows),
        }

    @router.post("/refresh_corpus_hints", response_model=RefreshCorpusHintsResponse)
    async def refresh_corpus_hints(
        request: RefreshCorpusHintsRequest,
    ) -> RefreshCorpusHintsResponse:
        """Refresh corpus hints, optionally for a single scope with tuning params."""
        from services.rag.corpus_hints import update_corpus_hints

        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")
        eb = get_event_bus_fn() if get_event_bus_fn else None

        bl_override: frozenset[str] | None = None
        if request.blocklist_override is not None:
            bl_override = frozenset(t.lower() for t in request.blocklist_override)

        extra_bl = frozenset[str]()
        if request.extra_blocklist:
            extra_bl = frozenset(t.lower() for t in request.extra_blocklist)

        result = await update_corpus_hints(
            prop_idx,
            scope=request.scope,
            entity_boost_hyphen=request.entity_boost_hyphen,
            entity_boost_single=request.entity_boost_single,
            blocklist_override=bl_override,
            extra_blocklist=extra_bl,
            event_bus=eb,
        )
        if result:
            await prop_idx.stamp_watermark("corpus_hints")
        terms_by_scope = {
            s: len(
                [t for t in (csv or "").split(",") if t.strip()]
            )  # Ensure csv is a string
            for s, csv in result.items()
        }
        logger.info(
            "Corpus hints refreshed: scope=%s scopes_updated=%s",
            request.scope or "(all)",
            sorted(result),
        )
        return RefreshCorpusHintsResponse(
            scopes_updated=sorted(result),
            terms_by_scope=terms_by_scope,
        )

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
        row = prop_idx.get_article_row(request.source_path)
        if refresh_article_registry_from_row_fn is not None:
            refresh_article_registry_from_row_fn(row)
        logger.info(
            "Article %s: source_path=%s title=%s",
            "created" if created else "updated",
            request.source_path,
            request.title[:60] if request.title else "(empty)",
        )
        event_bus = get_event_bus_fn() if get_event_bus_fn else None
        if event_bus:
            await event_bus.publish_async_nowait(
                rag_article_upserted(
                    source_path=request.source_path,
                    created=created,
                    title=request.title,
                    content_hash=request.content_hash,
                )
            )
        return ArticleUpsertResponse(source_path=request.source_path, created=created)

    return router
