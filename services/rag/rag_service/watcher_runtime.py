"""Watcher, extraction worker, and post-activation background cleanup after dependency gates pass."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from services.rag.config import DEFAULT_INDEX_WORKERS, RagConfig
from services.rag.events.lifecycle import rag_post_index_stale
from services.rag.extraction_admission import ExtractionAdmissionGate
from services.rag.extraction_worker import run_extraction_worker
from services.rag.watcher_manager import WatcherManager

from . import indexing, state
from .background_tasks import track_background_task
from .lifecycle_constants import POST_INDEX_STEPS
from .scope_freshness import _post_reconcile_scope_freshness
from .startup_cleanup import (
    _purge_excluded_sources,
    _purge_orphans,
    _reconcile_pending,
)

if TYPE_CHECKING:
    from services.rag.models import DeleteResult, IndexResult

logger = logging.getLogger(__name__)


async def _start_watcher_runtime(config: RagConfig) -> None:
    """Start watcher runtime after all external activation gates have succeeded."""

    async def _watcher_index_fn(
        path: Path,
        chunk_tokens: int | None,
        *,
        emit_skip_event: bool = True,
    ) -> IndexResult:
        return await indexing._index_file(
            path,
            chunk_tokens=chunk_tokens,
            emit_skip_event=emit_skip_event,
        )

    async def _watcher_delete_fn(path: Path) -> DeleteResult:
        return await indexing._delete_file(path)

    worker_count = (
        config.index_workers
        if isinstance(config.index_workers, int)
        else DEFAULT_INDEX_WORKERS
    )

    reconcile_worker_count = (
        config.reconcile_workers if isinstance(config.reconcile_workers, int) else 3
    )
    state._watcher_manager = WatcherManager(
        index_fn=_watcher_index_fn,
        delete_fn=_watcher_delete_fn,
        event_bus=state._event_bus,
        index_workers=worker_count,
        reconcile_workers=reconcile_worker_count,
        reconcile_interval_s=config.reconcile_interval_s,
        post_reconcile_repair=_post_reconcile_scope_freshness,
        property_index=state._property_index,
    )

    post_index_steps = list(POST_INDEX_STEPS)
    if state._property_index is not None:
        stale = state._property_index.check_watermarks(post_index_steps)
        if stale:
            logger.error(
                "Post-index enrichment stale after last reindex: %s  "
                "Run: tasks/runbooks/rag-post-index-refresh.md",
                stale,
            )
            if state._event_bus is not None:
                await state._event_bus.publish(rag_post_index_stale(stale_steps=stale))
            if config.post_index_enforcement != "warn":
                state._post_index_stale = True

    for coro, name in (
        (_reconcile_pending(config), "rag-reconcile-pending"),
        (_purge_orphans(config), "rag-orphan-purge"),
        (_purge_excluded_sources(config), "rag-exclusion-purge"),
    ):
        track_background_task(asyncio.create_task(coro, name=name))

    await state._watcher_manager.start(config)

    state._extraction_shutdown = asyncio.Event()
    extraction_pipeline_id = state._config.knowledge_extraction.pipeline
    if extraction_pipeline_id:
        state._extraction_admission_gate = ExtractionAdmissionGate(
            pipeline_id=extraction_pipeline_id,
            event_bus=state._event_bus,
        )
        state._extraction_admission_gate.start()
        logger.info(
            "ExtractionAdmissionGate started (pipeline=%s)",
            extraction_pipeline_id,
        )
    extraction_task = asyncio.create_task(
        run_extraction_worker(
            config=config,
            collection_fn=state._get_collection,
            property_index=state._property_index,
            event_bus=state._event_bus,
            shutdown_event=state._extraction_shutdown,
            admission_gate=state._extraction_admission_gate,
        ),
        name="rag-extraction-worker",
    )
    track_background_task(extraction_task)
