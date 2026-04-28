"""File and directory indexing routes: /index, /reindex, /index_directory, etc."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from collections.abc import Callable

    import chromadb
    from universal_event_bus import EventBus

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex

from services.rag.admin_routes._helpers import (
    DEFAULT_EXTENSIONS,
    _align_list_length,
    _clear_directory_sources,
    _validate_directory,
    _validate_file,
)
from services.rag.directory_ops import (
    IndexFileFn,
    collect_directory_candidates,
    delete_sources,
    find_removed_directory_sources,
    index_directory_contents,
)
from services.rag.events.indexing import (
    rag_directory_cleared,
    rag_directory_index_completed,
    rag_directory_index_started,
)
from services.rag.models import (
    ClearDirectoryRequest,
    ClearDirectoryResponse,
    IndexDirectoryRequest,
    IndexDirectoryResponse,
    IndexRequest,
    IndexResult,
    SourceResponse,
    SourcesResponse,
    StatsResponse,
)

logger = logging.getLogger(__name__)


def register_indexing_routes(
    router: APIRouter,
    *,
    index_file_fn: IndexFileFn,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_property_index_fn: Callable[[], PropertyIndex | None],
    collection_name: str,
    get_event_bus_fn: Callable[[], EventBus | None] | None = None,
    get_config_fn: Callable[[], RagConfig | None] | None = None,
    **_kwargs: object,
) -> None:
    """Register file/directory indexing and source-query routes onto router."""

    async def _index_single_file(
        request: IndexRequest, *, operation: str
    ) -> IndexResult:
        return await index_file_fn(
            _validate_file(request.path),
            request.metadata_overrides,
            force=request.force,
            operation_id=uuid4().hex,
            operation=operation,
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
            logger.info(
                "Force reindex upfront clear: path=%s sources=%d chunks=%d",
                dir_path,
                sources_cleared,
                chunks_cleared,
            )

        file_paths, walked_sources = collect_directory_candidates(
            dir_path=dir_path,
            extensions=extensions,
            collect_walked_sources=is_reindex,
        )
        candidate_count = len(file_paths)
        if eb:
            await eb.publish_nowait(
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
            operation="reindex" if is_reindex else "index",
            max_concurrency=(
                config.index_workers
                if (config := get_config_fn()) is not None
                else None
            ),
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
            except Exception:
                raise

        prop_idx = get_property_index_fn()
        if prop_idx is not None:
            await prop_idx.stamp_watermark("reindex")

        if eb:
            await eb.publish_nowait(
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
        return await _index_single_file(request, operation="index")

    @router.post("/reindex", response_model=IndexResult)
    async def reindex_file(request: IndexRequest) -> IndexResult:
        # Keep /reindex as an explicit alias for operational clarity.
        return await _index_single_file(request, operation="reindex")

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
        logger.info(
            "Directory cleared: path=%s sources=%d chunks=%d",
            dir_path,
            sources_cleared,
            chunks_cleared,
        )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_nowait(
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
