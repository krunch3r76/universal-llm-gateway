"""Status and monitoring routes: /indexing/status, /coverage, /source-status, /watch/status."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    from collections.abc import Callable

    import chromadb

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex
    from services.rag.watcher_manager import WatcherManager

from services.rag.admin_routes._extraction_export import (
    register_extraction_export_route,
)
from services.rag.admin_routes._helpers import (
    _build_source_status_item,
    _coverage_sources,
    _resolve_source_status_paths,
)
from services.rag.models import (
    CoverageResponse,
    IndexingStatusResponse,
    PrefixCoverage,
    ScopeCoverage,
    SourceStatusResponse,
    WatcherStatusItem,
)

logger = logging.getLogger(__name__)


def register_status_routes(
    router: APIRouter,
    *,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_watcher_manager_fn: Callable[[], WatcherManager | None],
    get_property_index_fn: Callable[[], PropertyIndex | None],
    collection_name: str,
    get_config_fn: Callable[[], RagConfig | None] | None = None,
    **_kwargs: object,
) -> None:
    """Register status and monitoring routes onto router."""

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
        indexing_failures_permanent_count = 0
        indexing_failures_transient_count = 0
        contextualize_cache_rows = 0
        contextualize_cache_rows_degraded = False
        stale_corpus_hints_count = 0
        stale_corpus_hints_count_degraded = False
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
                (
                    indexing_failures_permanent_count,
                    indexing_failures_transient_count,
                ) = prop_idx.get_indexing_failure_counts()
                try:
                    contextualize_cache_rows = prop_idx.count_contextualized_chunks()
                except Exception:
                    contextualize_cache_rows_degraded = True
                try:
                    stale_corpus_hints_count = (
                        prop_idx.count_scopes_with_stale_corpus_hints()
                    )
                except Exception:
                    stale_corpus_hints_count_degraded = True
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
            indexing_failures_permanent_count=indexing_failures_permanent_count,
            indexing_failures_transient_count=indexing_failures_transient_count,
            contextualize_cache_rows=contextualize_cache_rows,
            contextualize_cache_rows_degraded=contextualize_cache_rows_degraded,
            stale_corpus_hints_count=stale_corpus_hints_count,
            stale_corpus_hints_count_degraded=stale_corpus_hints_count_degraded,
        )

    @router.get("/coverage", response_model=CoverageResponse)
    def get_coverage() -> CoverageResponse:
        """Per-scope, per-prefix view of indexed file counts and recency.

        Aggregates indexed sources against configured scope prefixes.
        Enriches with last_indexed from SQLite ``indexed_sources.updated_at``.
        """
        config = get_config_fn() if get_config_fn else None
        if config is None:
            return CoverageResponse(scopes={})

        prop_idx = get_property_index_fn()

        source_last_indexed: dict[str, str] = {}
        timestamp_scan_degraded = False

        if prop_idx is not None:
            try:
                source_last_indexed = prop_idx.get_indexed_sources_with_timestamps()
            except Exception as e:
                logger.warning(
                    "Coverage: SQLite timestamp fetch failed, omitting last_indexed: %s",
                    e,
                    exc_info=True,
                )
                timestamp_scan_degraded = True

        all_sources = _coverage_sources(
            prop_idx=prop_idx,
            chroma_sources=set(source_last_indexed),
        )

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

    @router.get("/source-status", response_model=SourceStatusResponse)
    def get_source_status(
        sources: list[str] | None = Query(
            None, description="source_path values to query"
        ),
        arxiv_ids: list[str] | None = Query(
            None, description="arXiv IDs to resolve (e.g. 2402.16667)"
        ),
        filenames: list[str] | None = Query(
            None, description="Basenames to resolve via articles.filename"
        ),
    ) -> SourceStatusResponse:
        """Return point-in-time pipeline status for one or more source files.

        Accepts explicit ``source_path`` values and/or resolves ``arxiv_ids`` and
        ``filenames`` to paths before deriving pipeline stage from live SQLite
        state. Returns queue details, article metadata, filesystem presence,
        indexing timestamp, and contextualized chunk count. Also returns aggregate
        queue depth and stale vocabulary scope count for classify_vocabulary
        readiness signalling.

        Sources not found in any table are returned with pipeline_stage='registered'.
        """
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")

        if not any((sources, arxiv_ids, filenames)):
            raise HTTPException(
                status_code=422,
                detail="Provide at least one of: sources, arxiv_ids, filenames",
            )

        resolved_paths = _resolve_source_status_paths(
            prop_idx,
            sources=sources,
            arxiv_ids=arxiv_ids,
            filenames=filenames,
        )

        queue_depth: int = prop_idx.get_extraction_queue_count()
        items = [_build_source_status_item(path, prop_idx) for path in resolved_paths]
        stale_corpus_hints_count: int = prop_idx.count_scopes_with_stale_corpus_hints()
        return SourceStatusResponse(
            sources=items,
            queue_depth=queue_depth,
            frontier_status="unknown",
            stale_corpus_hints_count=stale_corpus_hints_count,
        )

    register_extraction_export_route(router, get_collection_fn=get_collection_fn)
