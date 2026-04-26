"""RAG service lifecycle orchestration.

Startup initializes storage, embeddings, event bus, optional article registry,
and watcher scheduling. Shutdown tears these resources down in reverse order.
This module also owns startup-only background recovery tasks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import chromadb
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from services.rag.admission_gate import AdmissionGate
from services.rag.article_registry import (
    load_registry as load_article_registry_yaml,
)
from services.rag.article_registry import (
    load_registry_from_db,
    replace_article_rows,
    to_article_rows,
)
from services.rag.config import load_config
from services.rag.embeddings import close as close_embeddings
from services.rag.embeddings import configure as configure_embeddings
from services.rag.embeddings import set_event_bus as set_embeddings_event_bus
from services.rag.events.indexing import (
    rag_contextualize_cache_gc_completed,
    rag_contextualize_cache_gc_failed,
)
from services.rag.events.lifecycle import (
    rag_article_registry_failed,
    rag_article_registry_loaded,
    rag_shutdown,
    rag_started,
)
from services.rag.model_availability_tracker import (
    ModelAvailabilityTracker,
    close_model_availability_client,
    get_model_availability_tracker,
    set_model_availability_tracker,
)
from services.rag.property_index import PropertyIndex

from . import state
from .background_tasks import track_background_task
from .dependency_activation import _activate_dependencies_when_ready
from .extraction_runtime import stop_extraction_runtime
from .scope_freshness import (
    _post_reconcile_scope_freshness,
    _run_startup_scope_freshness_repair,
    _watcher_debounced_scope_freshness,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_post_reconcile_scope_freshness",
    "_run_startup_scope_freshness_repair",
    "_shutdown",
    "_startup",
    "_watcher_debounced_scope_freshness",
]


async def _startup() -> None:
    """Initialize local runtime state, then activate Stargate-backed dependencies asynchronously."""
    store_path = Path.home() / ".rag" / "store"
    store_path.mkdir(parents=True, exist_ok=True)
    state._dependency_activation.phase = "booting"
    state._dependency_activation.attempts = 0
    state._dependency_activation.waiting_on = None
    state._dependency_activation.last_error = None

    state._chroma = chromadb.PersistentClient(path=str(store_path))
    state._collection = state._chroma.get_or_create_collection(
        name=state.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    state._event_bus = EventBus()
    state._broadcaster = MinimalEventDebugBroadcaster(
        uds_publish_path=os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        ),
    )
    state._event_bus.set_debug_broadcaster(state._broadcaster)
    await state._broadcaster.start_debug_server()

    state._config = load_config()
    configure_embeddings(state._config.embedding_model)
    set_embeddings_event_bus(state._event_bus)

    if state._config.contextualize_model:
        state._admission_gate = AdmissionGate([state._config.contextualize_model])
        state._admission_gate.start()
        logger.info(
            "AdmissionGate started (model=%s)",
            state._config.contextualize_model,
        )

    ke = state._config.knowledge_extraction
    watch_ids = [
        state._config.embedding_model,
        ke.extraction_model,
        ke.pipeline,
    ]
    tracker = ModelAvailabilityTracker()
    set_model_availability_tracker(tracker)
    await tracker.configure(watch_ids)

    state._property_index = PropertyIndex()
    await state._property_index.start()

    # Non-fatal backstop GC for orphaned contextualize cache rows —
    # primary cleanup happens inside remove_source_metadata; this sweep
    # recovers from crashes between delete paths.
    try:
        deleted_cache_rows = (
            await state._property_index.garbage_collect_contextualized_chunks()
        )
        logger.info(
            "Contextualize cache startup GC complete (deleted_rows=%d)",
            deleted_cache_rows,
        )
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_contextualize_cache_gc_completed(deleted_rows=deleted_cache_rows)
            )
    except Exception as gc_exc:
        logger.warning("Contextualize cache startup GC failed: %s", gc_exc)
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_contextualize_cache_gc_failed(
                    error=f"{type(gc_exc).__qualname__}: {gc_exc}",
                )
            )

    db_path = state._property_index.db_path
    try:
        state._registry = await asyncio.to_thread(load_registry_from_db, db_path)
        legacy_path = state._config.article_registry_path
        if not state._registry and legacy_path is not None and legacy_path.exists():
            logger.info(
                "Articles table empty; importing one-time legacy registry from %s",
                legacy_path,
            )
            yaml_registry = await asyncio.to_thread(
                load_article_registry_yaml, legacy_path
            )
            if yaml_registry is not None:
                rows = to_article_rows(
                    yaml_registry,
                    source_root=legacy_path.parent,
                    scope_resolver=state._config.get_scope_for_path,
                )
                await asyncio.to_thread(replace_article_rows, db_path, rows)
                state._registry = yaml_registry
        if state._event_bus is not None:
            await state._event_bus.publish(
                rag_article_registry_loaded(
                    path=str(db_path),
                    article_count=len(state._registry) if state._registry else 0,
                )
            )
    except Exception as e:
        logger.error(
            "Failed to load article registry from metadata DB %s: %s",
            db_path,
            e,
            exc_info=True,
        )
        if state._event_bus is not None:
            await state._event_bus.publish(
                rag_article_registry_failed(
                    path=str(db_path),
                    error=str(e),
                )
            )
        state._registry = None

    await state._event_bus.publish(rag_started())

    state._dependency_activation.phase = "activating"
    state._dependency_activation.waiting_on = "stargate"
    state._init_task = asyncio.create_task(
        _activate_dependencies_when_ready(state._config),
        name="rag-dependency-activation",
    )
    track_background_task(state._init_task)


async def _shutdown() -> None:
    """Shutdown RAG resources, cancel lifecycle tasks, and stop background services."""
    state._dependency_activation.phase = "shutting_down"

    await stop_extraction_runtime()

    tasks = [task for task in state._background_tasks if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    state._background_tasks.clear()
    state._init_task = None

    if state._admission_gate is not None:
        await state._admission_gate.stop()
        state._admission_gate = None
    if state._event_bus is not None:
        await state._event_bus.publish(rag_shutdown())
    if state._watcher_manager is not None:
        await state._watcher_manager.stop()
        state._watcher_manager = None
    if state._property_index is not None:
        await state._property_index.stop()
        state._property_index = None
    mat = get_model_availability_tracker()
    if mat is not None:
        await mat.stop()
        set_model_availability_tracker(None)
    await close_model_availability_client()
    await close_embeddings()
    if state._broadcaster is not None:
        await state._broadcaster.stop_debug_server()
        state._broadcaster = None
    state._event_bus = None
