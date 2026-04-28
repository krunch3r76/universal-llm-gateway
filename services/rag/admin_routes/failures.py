"""Indexing failure management routes: /indexing_failures."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    from collections.abc import Callable

    from universal_event_bus import EventBus

    from services.rag.property_index import IndexingFailure, PropertyIndex
    from services.rag.watcher_manager import WatcherManager

from services.rag.events.indexing import (
    rag_file_indexing_failure_cleared,
    rag_file_indexing_failure_retry_requested,
)
from services.rag.models import (
    DeleteIndexingFailureResponse,
    IndexingFailureResponse,
    IndexingFailuresListResponse,
    RetryIndexingFailureResponse,
)

logger = logging.getLogger(__name__)


def _failure_to_response(failure: IndexingFailure) -> IndexingFailureResponse:
    return IndexingFailureResponse(
        source=failure.source,
        failure_category=failure.failure_category,
        failure_reason=failure.failure_reason,
        error_message=failure.error_message,
        error_type=failure.error_type,
        first_failed_at=failure.first_failed_at,
        last_failed_at=failure.last_failed_at,
        attempt_count=failure.attempt_count,
        source_hash=failure.source_hash,
        source_size_bytes=failure.source_size_bytes,
        source_mtime_ns=failure.source_mtime_ns,
    )


def register_failure_routes(
    router: APIRouter,
    *,
    get_property_index_fn: Callable[[], PropertyIndex | None],
    get_watcher_manager_fn: Callable[[], WatcherManager | None],
    get_event_bus_fn: Callable[[], EventBus | None] | None = None,
    **_kwargs: object,
) -> None:
    """Register indexing failure listing, deletion, and retry routes onto router."""

    @router.get("/indexing_failures", response_model=IndexingFailuresListResponse)
    async def list_indexing_failures(
        category: str = Query(default="all"),
    ) -> IndexingFailuresListResponse:
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")
        if category not in ("all", "permanent", "transient"):
            raise HTTPException(
                status_code=400,
                detail="category must be 'all', 'permanent', or 'transient'",
            )
        cat_filter = None if category == "all" else category
        rows = prop_idx.list_indexing_failures(category=cat_filter)
        items = [_failure_to_response(r) for r in rows]
        return IndexingFailuresListResponse(failures=items, count=len(items))

    @router.delete(
        "/indexing_failures/{source:path}",
        response_model=DeleteIndexingFailureResponse,
    )
    async def delete_indexing_failure(source: str) -> DeleteIndexingFailureResponse:
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")
        deleted = await prop_idx.clear_indexing_failure(source)
        if not deleted:
            raise HTTPException(
                status_code=404, detail=f"No indexing failure for source: {source}"
            )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_nowait(
                rag_file_indexing_failure_cleared(
                    file=source, reason="operator_cleared"
                )
            )
        return DeleteIndexingFailureResponse(source=source, deleted=True)

    @router.post(
        "/indexing_failures/{source:path}/retry",
        response_model=RetryIndexingFailureResponse,
    )
    async def retry_indexing_failure(source: str) -> RetryIndexingFailureResponse:
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")
        cleared = await prop_idx.clear_indexing_failure(source)
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if cleared and eb:
            await eb.publish_nowait(
                rag_file_indexing_failure_cleared(
                    file=source, reason="operator_cleared"
                )
            )
        wm = get_watcher_manager_fn()
        scheduled = False
        if wm is not None:
            scheduled = await wm.request_reindex(Path(source))
        if eb:
            await eb.publish_nowait(
                rag_file_indexing_failure_retry_requested(
                    file=source, scheduled=scheduled
                )
            )
        return RetryIndexingFailureResponse(
            source=source, cleared=cleared, scheduled=scheduled
        )
