"""RAG service lifecycle orchestration.

Startup initializes storage, embeddings, event bus, optional article registry,
and watcher scheduling. Shutdown tears these resources down in reverse order.
This module also owns startup-only background recovery tasks.
"""

from __future__ import annotations

import asyncio
import logging
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

import chromadb
import httpx
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from services.rag.article_registry import (
    load_registry as load_article_registry_yaml,
)
from services.rag.article_registry import (
    load_registry_from_db,
    replace_article_rows,
    to_article_rows,
)
from services.rag.config import DEFAULT_INDEX_WORKERS, RagConfig, load_config
from services.rag.corpus_hints import (
    detect_stale_scopes,
    scopes_touching_watch_path,
)
from services.rag.directory_ops import (
    delete_sources,
    find_sources_under_prefixes,
    purge_orphaned_sources,
)
from services.rag.embeddings import close as close_embeddings
from services.rag.embeddings import configure as configure_embeddings
from services.rag.embeddings import set_event_bus as set_embeddings_event_bus
from services.rag.embeddings import wait_until_healthy
from services.rag.events.extraction import rag_extraction_unavailable
from services.rag.events.lifecycle import (
    rag_article_registry_failed,
    rag_article_registry_loaded,
    rag_embeddings_unavailable,
    rag_exclusion_purged,
    rag_orphan_purged,
    rag_pending_reconciled,
    rag_post_index_stale,
    rag_shutdown,
    rag_started,
)
from services.rag.knowledge_extractor import (
    configure_timeouts,
    configure_tracker,
    wait_until_extraction_ready,
)
from services.rag.property_index import PropertyIndex
from services.rag.vocabulary import configured_scopes_map, run_scope_freshness_repair
from services.rag.watcher_manager import WatcherManager

from . import indexing, state

if TYPE_CHECKING:
    from services.rag.models import DeleteResult, IndexResult

logger = logging.getLogger(__name__)

_RECONCILE_FILE_TIMEOUT_S = 300.0

_POST_INDEX_STEPS = ("corpus_hints", "vocabulary", "noise")


def _maybe_clear_post_index_stale_after_repair(config: RagConfig) -> None:
    """If strict gate was set at startup, clear it once watermarks catch up."""
    if not state._post_index_stale:
        return
    still = state._property_index.check_watermarks(
        list(_POST_INDEX_STEPS), reference="reindex"
    )
    if not still:
        state._post_index_stale = False
        logger.info("Post-index strict gate cleared after automatic scope repair")


async def _run_startup_scope_freshness_repair(config: RagConfig) -> None:
    pi = state._property_index
    if pi is None:
        return
    stale = detect_stale_scopes(
        property_index=pi,
        configured_scopes=configured_scopes_map(config),
        scope_filter=None,
    )
    if not stale:
        return
    logger.warning(
        "Automatic scope freshness repair (startup): %d stale scopes: %s",
        len(stale),
        stale,
    )
    await run_scope_freshness_repair(
        property_index=pi,
        config=config,
        stale_scopes=stale,
        event_bus=state._event_bus,
        trigger="startup",
    )
    _maybe_clear_post_index_stale_after_repair(config)


async def _post_reconcile_scope_freshness(prefix_paths: list[str]) -> None:
    cfg = state._config
    pi = state._property_index
    if cfg is None or pi is None or not prefix_paths:
        return
    affected: set[str] = set()
    for p in prefix_paths:
        affected |= scopes_touching_watch_path(cfg, Path(p))
    scope_filter = affected if affected else None
    stale = detect_stale_scopes(
        property_index=pi,
        configured_scopes=configured_scopes_map(cfg),
        scope_filter=scope_filter,
    )
    if not stale:
        return
    logger.warning(
        "Automatic scope freshness repair (reconcile): %d stale scopes: %s",
        len(stale),
        stale,
    )
    await run_scope_freshness_repair(
        property_index=pi,
        config=cfg,
        stale_scopes=stale,
        event_bus=state._event_bus,
        trigger="reconcile",
    )
    _maybe_clear_post_index_stale_after_repair(cfg)


async def _watcher_debounced_scope_freshness(scopes: set[str]) -> None:
    cfg = state._config
    pi = state._property_index
    if cfg is None or pi is None or not scopes:
        return
    stale = detect_stale_scopes(
        property_index=pi,
        configured_scopes=configured_scopes_map(cfg),
        scope_filter=scopes,
    )
    if not stale:
        return
    logger.warning(
        "Automatic scope freshness repair (watcher): %d stale scopes: %s",
        len(stale),
        stale,
    )
    await run_scope_freshness_repair(
        property_index=pi,
        config=cfg,
        stale_scopes=stale,
        event_bus=state._event_bus,
        trigger="watcher",
    )
    _maybe_clear_post_index_stale_after_repair(cfg)


async def _startup() -> None:
    """Initialize runtime resources required by request handlers and watchers."""
    store_path = Path.home() / ".rag" / "store"
    store_path.mkdir(parents=True, exist_ok=True)
    state._chroma = chromadb.PersistentClient(path=str(store_path))
    state._collection = state._chroma.get_or_create_collection(
        name=state.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    state._event_bus = EventBus()
    state._broadcaster = MinimalEventDebugBroadcaster(
        uds_publish_path="/tmp/universal-protocol/events.sock",
    )
    state._event_bus.set_debug_broadcaster(state._broadcaster)
    await state._broadcaster.start_debug_server()
    await state._event_bus.publish_async(rag_started())
    state._config = load_config()
    configure_embeddings(state._config.embedding_model)
    set_embeddings_event_bus(state._event_bus)
    state._property_index = PropertyIndex()
    await state._property_index.start()
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
            await state._event_bus.publish_async(
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
            await state._event_bus.publish_async(
                rag_article_registry_failed(
                    path=str(db_path),
                    error=str(e),
                )
            )
        state._registry = None
    if state._config.automatic_indexing_enabled and state._config.watch_directories:
        state._init_task = asyncio.create_task(
            _deferred_watcher_start(state._config), name="rag-watcher-init"
        )
    elif not state._config.automatic_indexing_enabled:
        logger.info(
            "Automatic indexing disabled (automatic_indexing_enabled: false) — watcher not started"
        )


async def _reconcile_pending(config: RagConfig) -> None:
    """Reconcile pending files after interrupted indexing operations."""
    if state._property_index is None:
        return
    try:
        pending_files = state._property_index.get_pending_files()
    except Exception:
        logger.error(
            "Failed to read pending files during startup reconciliation",
            exc_info=True,
        )
        return
    if not pending_files:
        return

    worker_count = (
        config.index_workers
        if isinstance(config.index_workers, int)
        else DEFAULT_INDEX_WORKERS
    )
    logger.warning(
        "Reconciling %d files pending from interrupted indexing",
        len(pending_files),
    )
    reconciled = cleared = failed_transient = failed_permanent = 0

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    for source in pending_files:
        file_path = Path(source)
        if not file_path.exists():
            await state._property_index.clear_pending(source)
            logger.info("Pending file removed (deleted): %s", source)
            cleared += 1
            continue
        queue.put_nowait(source)

    async def _reconcile_worker() -> None:
        nonlocal reconciled, failed_transient, failed_permanent
        while True:
            src = await queue.get()
            if src is None:
                queue.task_done()
                return
            try:
                ct = _resolve_chunk_tokens_for_file(Path(src), config)
                await asyncio.wait_for(
                    indexing._index_file(Path(src), chunk_tokens=ct),
                    timeout=_RECONCILE_FILE_TIMEOUT_S,
                )
                reconciled += 1
            except TimeoutError:
                logger.warning(
                    "Reconciliation timed out for %s after %.0fs; "
                    "will retry on next sweep",
                    src,
                    _RECONCILE_FILE_TIMEOUT_S,
                )
                failed_transient += 1
            except (
                ConnectionError,
                httpx.TimeoutException,
                httpx.ConnectError,
            ) as e:
                logger.warning(
                    "Transient error reconciling %s; will retry on next sweep: %r",
                    src,
                    e,
                )
                failed_transient += 1
            except httpx.HTTPStatusError as e:
                if e.response.is_server_error:
                    logger.warning(
                        "Transient %d reconciling %s; will retry on next sweep",
                        e.response.status_code,
                        src,
                    )
                    failed_transient += 1
                else:
                    logger.error(
                        "Permanent HTTP error reconciling %s: %s",
                        src,
                        e,
                    )
                    if state._property_index is not None:
                        await state._property_index.clear_pending(src)
                    failed_permanent += 1
            except Exception as e:
                logger.error(
                    "Permanent error reconciling %s; requires manual intervention: %s",
                    src,
                    e,
                    exc_info=True,
                )
                if state._property_index is not None:
                    await state._property_index.clear_pending(src)
                failed_permanent += 1
            finally:
                queue.task_done()

    n_workers = min(worker_count, len(pending_files))
    workers = [
        asyncio.create_task(_reconcile_worker(), name=f"reconcile-worker-{i}")
        for i in range(n_workers)
    ]

    if workers:
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)
        worker_results = await asyncio.gather(*workers, return_exceptions=True)
        for worker_result in worker_results:
            if isinstance(worker_result, BaseException):
                logger.error(
                    "Pending reconcile worker raised unexpectedly: %r",
                    worker_result,
                )

    if state._event_bus is not None:
        await state._event_bus.publish_async(
            rag_pending_reconciled(
                reconciled=reconciled,
                cleared=cleared,
                failed_transient=failed_transient,
                failed_permanent=failed_permanent,
            )
        )


async def _purge_orphans(config: RagConfig) -> None:
    """Delete watched sources whose backing files disappeared while service was down."""
    if state._collection is None or not config.watch_directories:
        return
    watch_prefixes = [
        str(Path(wd.path).expanduser().resolve()) + "/"
        for wd in config.watch_directories
    ]
    remove_source_fn = (
        state._property_index.remove_source_metadata if state._property_index else None
    )
    list_known_fn = (
        state._property_index.list_known_sources if state._property_index else None
    )
    if remove_source_fn is None or list_known_fn is None:
        logger.warning("Property index not available, skipping orphan purge.")
        return
    files_purged, chunks_purged, purged_sources = await purge_orphaned_sources(
        collection=state._collection,
        watch_prefixes=watch_prefixes,
        remove_source_metadata_fn=remove_source_fn,
        list_known_sources_fn=list_known_fn,
    )
    if files_purged > 0:
        logger.info(
            "Startup orphan purge complete: files=%d chunks=%d sources=%s",
            files_purged,
            chunks_purged,
            sorted(Path(s).name for s in purged_sources),
        )
    source_names = (
        sorted(Path(s).name for s in purged_sources) if purged_sources else None
    )
    if state._event_bus is not None:
        await state._event_bus.publish_async(
            rag_orphan_purged(
                files=files_purged, chunks=chunks_purged, sources=source_names
            )
        )


async def _purge_excluded_sources(config: RagConfig) -> None:
    """Delete indexed sources that now match exclusion patterns in watch config.

    ∀ watch_dir with exclude patterns: find indexed sources under that prefix,
    check filenames against fnmatch patterns, and purge matches. Covers the case
    where a file was previously indexed but later added to the exclude list.
    """
    if state._collection is None or not config.watch_directories:
        return
    sources_to_purge: set[str] = set()
    for wd in config.watch_directories:
        if not wd.exclude:
            continue
        watch_path = Path(wd.path).expanduser().resolve()
        prefix = str(watch_path) + "/"
        known = find_sources_under_prefixes(
            collection=state._collection,
            prefixes=[prefix],
            list_known_sources_fn=(
                state._property_index.list_known_sources
                if state._property_index is not None
                else None
            ),
        )
        for source in known:
            if any(fnmatch(Path(source).name, pat) for pat in wd.exclude):
                sources_to_purge.add(source)
    if not sources_to_purge:
        if state._event_bus is not None:
            await state._event_bus.publish_async(
                rag_exclusion_purged(files=0, chunks=0)
            )
        return
    remove_source_fn = (
        state._property_index.remove_source_metadata
        if state._property_index is not None
        else None
    )
    files_purged, chunks_purged = await delete_sources(
        collection=state._collection,
        sources=sources_to_purge,
        remove_source_metadata_fn=remove_source_fn,
    )
    if files_purged > 0:
        logger.info(
            "Startup exclusion purge: files=%d chunks=%d sources=%s",
            files_purged,
            chunks_purged,
            sorted(Path(s).name for s in sources_to_purge),
        )
    source_names = sorted(Path(s).name for s in sources_to_purge)
    if state._event_bus is not None:
        await state._event_bus.publish_async(
            rag_exclusion_purged(
                files=files_purged, chunks=chunks_purged, sources=source_names
            )
        )


async def _deferred_watcher_start(config: RagConfig) -> None:
    """Start watcher only after embeddings and extraction are healthy."""

    try:
        await wait_until_healthy()
    except TimeoutError as exc:
        logger.error(
            "Embedding endpoint not healthy after timeout — watcher not started. %s",
            exc,
        )
        if state._event_bus is not None:
            await state._event_bus.publish_async(
                rag_embeddings_unavailable(error=str(exc))
            )
        return

    try:
        await wait_until_extraction_ready(config.knowledge_extraction.pipeline)
    except TimeoutError as exc:
        logger.error(
            "Extraction pipeline not available after timeout — watcher not started. %s",
            exc,
        )
        if state._event_bus is not None:
            await state._event_bus.publish_async(
                rag_extraction_unavailable(
                    pipeline=config.knowledge_extraction.pipeline,
                    error=str(exc),
                )
            )
        return

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
    configure_timeouts(config.knowledge_extraction)
    tracker = configure_tracker(config.knowledge_extraction)
    await tracker.start()
    state._extraction_tracker = tracker
    state._watcher_manager = WatcherManager(
        index_fn=_watcher_index_fn,
        delete_fn=_watcher_delete_fn,
        event_bus=state._event_bus,
        index_workers=worker_count,
        reconcile_interval_s=config.reconcile_interval_s,
        file_timeout_s=config.file_timeout_s,
        post_reconcile_repair=_post_reconcile_scope_freshness,
        scope_repair_runner=_watcher_debounced_scope_freshness,
    )

    post_index_steps = list(_POST_INDEX_STEPS)
    if state._property_index is not None:
        stale = state._property_index.check_watermarks(post_index_steps)
        if stale:
            logger.error(
                "Post-index enrichment stale after last reindex: %s  "
                "Run: tasks/runbooks/rag-post-index-refresh.md",
                stale,
            )
            if state._event_bus is not None:
                await state._event_bus.publish_async(
                    rag_post_index_stale(stale_steps=stale)
                )
            if config.post_index_enforcement != "warn":
                state._post_index_stale = True

    await _run_startup_scope_freshness_repair(config)

    for coro, name in (
        (_reconcile_pending(config), "rag-reconcile-pending"),
        (_purge_orphans(config), "rag-orphan-purge"),
        (_purge_excluded_sources(config), "rag-exclusion-purge"),
    ):
        task = asyncio.create_task(coro, name=name)
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)

    await state._watcher_manager.start(config)


def _resolve_chunk_tokens_for_file(file_path: Path, config: RagConfig) -> int | None:
    """Resolves the chunk token override for a file based on watch directory configurations.

    Checks if the file is within a watched directory, considering recursion, extensions,
    and exclusion patterns. Returns the `chunk_tokens` from the matching watch directory
    or `None` if no override applies.

    Args:
        file_path: The path to the file.
        config: The RAG configuration.

    Returns:
        The `chunk_tokens` override (int) or `None`.
    """
    resolved_file = file_path.expanduser().resolve()
    baseline: set[str] = {f".{ext.lower()}" for ext in config.baseline_extensions}
    for watch_directory in config.watch_directories:
        watch_path = Path(watch_directory.path).expanduser().resolve()
        if not resolved_file.is_relative_to(watch_path):
            continue
        if not watch_directory.recursive and resolved_file.parent != watch_path:
            continue
        effective_extensions: set[str] = (
            {f".{ext.lower()}" for ext in watch_directory.extensions}
            if watch_directory.extensions
            else baseline
        )
        if resolved_file.suffix.lower() not in effective_extensions:
            continue
        if any(fnmatch(resolved_file.name, pat) for pat in watch_directory.exclude):
            continue
        return watch_directory.chunk_tokens
    return None


async def _shutdown() -> None:
    """Shutdown RAG resources and stop background services."""
    if state._event_bus is not None:
        await state._event_bus.publish_async(rag_shutdown())
        if state._broadcaster is not None:
            await state._broadcaster.stop_debug_server()
            state._broadcaster = None
        state._event_bus = None
    if state._property_index is not None:
        await state._property_index.stop()
        state._property_index = None
    await close_embeddings()
    if state._watcher_manager is not None:
        await state._watcher_manager.stop()
        state._watcher_manager = None
    if state._extraction_tracker is not None:
        await state._extraction_tracker.stop()
        state._extraction_tracker = None
