"""RAG service — FastAPI application for indexing, search, and structured knowledge retrieval.

Architecture layers (index time):
  1. Chunking       — ``chunkers.py`` splits files into semantically coherent chunks using
                      target+pad sizing with paragraph overlap and heading injection.
  2. Embedding      — chunks are embedded via the configured embedding model (e.g.
                      qwen3-embedding-8b-q8-0) and stored in ChromaDB with cosine space.
  3. Extraction     — ``extraction_wiring.py`` calls the rag-extraction LLM pipeline to
                      extract entities, types, facets, topics, and relations from each chunk.
                      Results are stored both in ChromaDB chunk metadata and in the
                      SQLite-backed property inverted index (``property_index.py``).
  4. Pending journal — ``property_index.pending`` tracks in-flight indexing operations.
                      On restart, interrupted files are re-indexed before the watcher starts,
                      eliminating the window where the property index can hold dangling pointers.

Architecture layers (search time):
  5. Vector search  — ChromaDB cosine similarity retrieves the top-k candidate chunks.
  6. Property boost — ``search_scope.py`` queries the property index for entity/topic/relation
                      matches and applies a configurable score boost to matching chunks,
                      implementing hybrid structured+vector search.
  7. Recency sort   — ``search_scope.py`` applies an additive recency weight based on
                      ``indexed_at`` timestamps, favouring recently changed documents.

These layers are composed in ``_index_file_impl`` (index path) and ``search`` (query path).
The pipeline layer (``rag-context-v1``, ``project-assistant-v1``) handles query rewriting,
RRF multi-query merge, and answer generation on top of this service.

Invariants:
  ∀ upsert: all chunks of a file are committed in one batch (all-or-nothing extraction coherence).
  ∀ file ∈ pending: property index may be ahead of ChromaDB — re-index on next startup.
  ∀ file ∉ pending ∧ all_ids_match_prefix: both stores are consistent.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path

import chromadb
import httpx
from fastapi import FastAPI, HTTPException
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from services.rag.admin_routes import register_admin_routes
from services.rag.article_registry import (
    ArticleEntry,
)
from services.rag.article_registry import (
    get_entry as get_article_entry,
)
from services.rag.article_registry import (
    load_registry as load_article_registry,
)
from services.rag.article_registry import (
    lookup_article as lookup_article_metadata,
)
from services.rag.chunk_filters import chunk_is_junk
from services.rag.chunkers import Chunk, chunk_file
from services.rag.config import DEFAULT_INDEX_WORKERS, RagConfig, load_config
from services.rag.contextualize import contextualize_chunks
from services.rag.corpus_hints import update_corpus_hints
from services.rag.directory_ops import purge_orphaned_chunks
from services.rag.embeddings import (
    EmbeddingTransientError,
    embed_chunks,
    embed_query,
    wait_until_healthy,
)
from services.rag.embeddings import (
    close as close_embeddings,
)
from services.rag.embeddings import (
    configure as configure_embeddings,
)
from services.rag.embeddings import (
    set_event_bus as set_embeddings_event_bus,
)
from services.rag.events.extraction import rag_extraction_model_mismatch
from services.rag.events.indexing import (
    rag_article_content_hash_mismatch,
    rag_contextualization_applied,
    rag_file_deleted,
    rag_file_indexed,
    rag_file_indexing_failed,
    rag_file_skipped,
    rag_html_normalization_completed,
    rag_html_normalization_failed,
    rag_html_normalization_started,
    rag_property_index_unavailable,
)
from services.rag.events.lifecycle import (
    rag_article_registry_failed,
    rag_article_registry_loaded,
    rag_orphan_purged,
    rag_pending_reconciled,
    rag_post_index_stale,
    rag_shutdown,
    rag_started,
)
from services.rag.events.query import (
    rag_corpus_hints_update_failed,
    rag_scope_rejected,
    rag_scope_resolved,
    rag_scopes_listed,
    rag_search_embedding_failed,
    rag_search_executed,
    rag_search_no_results,
)
from services.rag.extraction_wiring import (
    ExtractionResult,
    recover_missing_extraction,
    run_extraction,
)
from services.rag.indexing_helpers import (
    all_ids_match_prefix,
    check_pdf_duplicate,
    file_hash,
)
from services.rag.knowledge_extractor import configure_timeouts
from services.rag.models import (
    ChunkByIndexItem,
    ChunksByIndexRequest,
    ChunksByIndexResponse,
    DeleteResult,
    FailedChunkItem,
    FailedExtractionResponse,
    IndexResult,
    ScopeInfo,
    ScopesResponse,
    SearchRequest,
    SearchResponse,
)
from services.rag.property_index import PropertyIndex
from services.rag.search_scope import (
    apply_bm25_sidecar,
    apply_max_distance_filter,
    apply_property_boost,
    apply_recency_sort,
    apply_source_prefix_filter_with_ids,
    require_loaded_config,
    resolve_scope_request,
)
from services.rag.watcher_manager import WatcherManager

logger = logging.getLogger(__name__)

# ChromaDB collection name for knowledge chunks.
COLLECTION_NAME = "knowledge"

app = FastAPI(title="RAG Service")

_chroma: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None
_watcher_manager: WatcherManager | None = None
_event_bus: EventBus | None = None
_broadcaster: MinimalEventDebugBroadcaster | None = None
_config: RagConfig | None = None
_init_task: asyncio.Task[None] | None = None
_property_index: PropertyIndex | None = None
_registry: dict[str, ArticleEntry] | None = None

# Serialize concurrent indexing of the same file path (watcher + API can race).
_file_index_locks: dict[str, asyncio.Lock] = {}
_post_index_stale: bool = False


def _article_event_kwargs(
    registry: dict[str, ArticleEntry], source: str
) -> dict[str, str]:
    """Return optional article metadata for rag_file_indexed document_metadata.

    Args:
        registry: Loaded article registry.
        source: Source file path being indexed.

    Returns:
        Metadata fields from registry entry, or empty dict when unmatched.
    """
    entry = get_article_entry(registry, source)
    if entry is None:
        return {}
    field_map = {
        "title": "article_title",
        "authors": "article_authors",
        "venue": "article_venue",
        "published_date": "published_date",
        "doi": "article_doi",
    }
    return {
        key: str(value)
        for attr, key in field_map.items()
        if (value := getattr(entry, attr))
    }


async def _maybe_update_corpus_hints() -> None:
    """Update corpus_hints.yaml from property index when config.corpus_hints_path is set.

    Logs on failure; does not raise so indexing is never failed by hints update.
    """
    if _config is None or _config.corpus_hints_path is None or _property_index is None:
        return
    try:
        await update_corpus_hints(
            _property_index,
            _config.corpus_hints_path,
            event_bus=_event_bus,
        )
    except Exception as e:
        logger.warning(
            "Failed to update corpus hints at %s: %s",
            _config.corpus_hints_path,
            e,
            exc_info=True,
        )
        if _event_bus is not None:
            await _event_bus.publish_async_nowait(
                rag_corpus_hints_update_failed(
                    path=str(_config.corpus_hints_path),
                    error=str(e),
                )
            )


def _get_collection() -> chromadb.Collection:
    """Return initialized ChromaDB collection."""
    assert _collection is not None, "Collection not initialized"
    return _collection


def get_event_bus() -> EventBus | None:
    """Return initialized event bus, if available.

    Returns:
        EventBus after startup, otherwise None.
    """
    return _event_bus


@app.on_event("startup")
async def _startup() -> None:
    global _chroma, _collection, _watcher_manager, _event_bus, _broadcaster
    global _config, _init_task, _property_index, _registry
    store_path = Path.home() / ".rag" / "store"
    store_path.mkdir(parents=True, exist_ok=True)
    _chroma = chromadb.PersistentClient(path=str(store_path))
    _collection = _chroma.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    _event_bus = EventBus()
    _broadcaster = MinimalEventDebugBroadcaster(
        persistence_config={
            "enabled": True,
            "directory": "/tmp/rag-events",
            "max_file_size_mb": 10,
            "max_files": 2,
            "flush_interval_seconds": 1.0,
        },
    )
    _event_bus.set_debug_broadcaster(_broadcaster)
    await _broadcaster.start_debug_server()
    await _event_bus.publish_async(rag_started())
    _config = load_config()
    configure_embeddings(_config.embedding_model)
    set_embeddings_event_bus(_event_bus)
    _property_index = PropertyIndex()
    await _property_index.start()
    if _config.article_registry_path is not None:
        try:
            _registry = await asyncio.to_thread(
                load_article_registry, _config.article_registry_path
            )
            if _event_bus is not None:
                await _event_bus.publish_async(
                    rag_article_registry_loaded(
                        path=str(_config.article_registry_path),
                        article_count=len(_registry),
                    )
                )
        except Exception as e:
            logger.error(
                "Failed to load article registry from %s: %s",
                _config.article_registry_path,
                e,
                exc_info=True,
            )
            if _event_bus is not None:
                await _event_bus.publish_async(
                    rag_article_registry_failed(
                        path=str(_config.article_registry_path),
                        error=str(e),
                    )
                )
            _registry = None
    if _config.automatic_indexing_enabled and _config.watch_directories:
        _init_task = asyncio.create_task(
            _deferred_watcher_start(_config), name="rag-watcher-init"
        )
    elif not _config.automatic_indexing_enabled:
        logger.info(
            "Automatic indexing disabled (automatic_indexing_enabled: false) — watcher not started"
        )


async def _deferred_watcher_start(config: RagConfig) -> None:
    """Start watchers after embedding endpoint is healthy.

    Runs as a background task so uvicorn binds the UDS socket immediately
    instead of blocking on the (potentially slow) initial reindex.
    """
    global _watcher_manager

    async def _watcher_index_fn(path: Path, chunk_tokens: int | None) -> IndexResult:
        return await _index_file(path, chunk_tokens=chunk_tokens)

    async def _watcher_delete_fn(path: Path) -> DeleteResult:
        return await _delete_file(path)

    worker_count = (
        config.index_workers
        if isinstance(config.index_workers, int)
        else DEFAULT_INDEX_WORKERS
    )
    configure_timeouts(config.knowledge_extraction)
    _watcher_manager = WatcherManager(
        index_fn=_watcher_index_fn,
        delete_fn=_watcher_delete_fn,
        event_bus=_event_bus,
        index_workers=worker_count,
    )
    try:
        await wait_until_healthy()
    except TimeoutError:
        logger.error(
            "Embedding endpoint not healthy after timeout; "
            "initial watcher sweep will be skipped for files requiring embedding"
        )

    # Reconcile files that were mid-index when the service was interrupted.
    # Runs after health check so embeddings are available; runs before the
    # watcher sweep so pending files are consistent before it sees them.
    if _property_index is not None:
        try:
            pending_files = _property_index.get_pending_files()
        except Exception:
            logger.error(
                "Failed to read pending files during startup reconciliation",
                exc_info=True,
            )
            pending_files = []
        if pending_files:
            logger.warning(
                "Reconciling %d files pending from interrupted indexing",
                len(pending_files),
            )
            reconciled = cleared = failed_transient = failed_permanent = 0

            queue: asyncio.Queue[str | None] = asyncio.Queue()
            for source in pending_files:
                file_path = Path(source)
                if not file_path.exists():
                    await _property_index.clear_pending(source)
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
                        await _index_file(Path(src), chunk_tokens=ct)
                        reconciled += 1
                    except (
                        TimeoutError,
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
                        if e.response.status_code in (502, 503, 504):
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
                            failed_permanent += 1
                    except Exception:
                        logger.error(
                            "Permanent error reconciling %s; requires manual intervention",
                            src,
                            exc_info=True,
                        )
                        if _property_index is not None:
                            await _property_index.clear_pending(src)
                        failed_permanent += 1
                    finally:
                        queue.task_done()

            n_workers = min(worker_count, queue.qsize())
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

            if _event_bus is not None:
                await _event_bus.publish_async(
                    rag_pending_reconciled(
                        reconciled=reconciled,
                        cleared=cleared,
                        failed_transient=failed_transient,
                        failed_permanent=failed_permanent,
                    )
                )

    # Purge chunks whose source files were deleted while the service was down.
    # The live watcher handles deletions in real time; this covers the gap
    # between shutdown and the next restart.
    if _collection is not None and config.watch_directories:
        watch_prefixes = [
            str(Path(wd.path).expanduser().resolve()) + "/"
            for wd in config.watch_directories
        ]
        remove_fn = (
            _property_index.remove_chunk if _property_index is not None else None
        )
        files_purged, chunks_purged = await purge_orphaned_chunks(
            collection=_collection,
            watch_prefixes=watch_prefixes,
            remove_chunk_fn=remove_fn,
        )
        if files_purged > 0:
            logger.info(
                "Startup orphan purge complete: files=%d chunks=%d",
                files_purged,
                chunks_purged,
            )
        if _event_bus is not None:
            await _event_bus.publish_async(
                rag_orphan_purged(files=files_purged, chunks=chunks_purged)
            )

    # Post-index watermark freshness check.
    post_index_steps = ["corpus_hints", "vocabulary", "bibliography"]
    if _property_index is not None:
        stale = _property_index.check_watermarks(post_index_steps)
        if stale:
            logger.error(
                "Post-index enrichment stale after last reindex: %s  "
                "Run: tasks/runbooks/rag-post-index-refresh.md",
                stale,
            )
            if _event_bus is not None:
                await _event_bus.publish_async(rag_post_index_stale(stale_steps=stale))
            if config.post_index_enforcement != "warn":
                global _post_index_stale
                _post_index_stale = True

    await _watcher_manager.start(config)


def _resolve_chunk_tokens_for_file(file_path: Path, config: RagConfig) -> int | None:
    """Resolve chunk_tokens for a file using watch-directory matching rules.

    Args:
        file_path: File path being indexed/reconciled.
        config: Active RAG configuration.

    Returns:
        Matching chunk_tokens override, or None when no watch directory matches.
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
        # First matching directory wins; overlapping watch dirs may have different chunk_tokens.
        return watch_directory.chunk_tokens
    return None


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Shutdown RAG resources and stop background services."""
    global _watcher_manager, _event_bus, _broadcaster, _property_index
    if _event_bus is not None:
        await _event_bus.publish_async(rag_shutdown())
    if _broadcaster is not None:
        await _broadcaster.stop_debug_server()
        _broadcaster = None
    _event_bus = None
    if _property_index is not None:
        await _property_index.stop()
        _property_index = None
    await close_embeddings()
    if _watcher_manager is not None:
        await _watcher_manager.stop()
        _watcher_manager = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CHARS_PER_TOKEN = 4  # Approximate characters per token for chunk sizing.


async def _index_file(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None = None,
    *,
    chunk_tokens: int | None = None,
    force: bool = False,
) -> IndexResult:
    """Index a file under a per-source lock.

    Args:
        file_path: File to index.
        metadata_overrides: Optional metadata fields merged into all chunks.
        chunk_tokens: Optional token budget override for chunking.
        force: If True, bypass unchanged checks.

    Returns:
        IndexResult for the file.
    """
    source = str(file_path)
    lock = _file_index_locks.setdefault(source, asyncio.Lock())
    try:
        async with lock:
            return await _index_file_impl(
                file_path, metadata_overrides, chunk_tokens, source, force=force
            )
    finally:
        if _file_index_locks.get(source) is lock:
            _file_index_locks.pop(source, None)


async def _delete_file(file_path: Path) -> DeleteResult:
    """Delete all indexed chunks for a removed file under per-source lock.

    Args:
        file_path: Source file path.

    Returns:
        DeleteResult with deleted chunk count.
    """
    source = str(file_path)
    lock = _file_index_locks.setdefault(source, asyncio.Lock())
    try:
        async with lock:
            return await _delete_file_impl(source)
    finally:
        if _file_index_locks.get(source) is lock:
            _file_index_locks.pop(source, None)


async def _delete_file_impl(source: str) -> DeleteResult:
    """Delete source chunks from ChromaDB and property index.

    Args:
        source: Canonical source path.

    Returns:
        DeleteResult with number of deleted chunk IDs.
    """
    collection = _get_collection()
    existing = collection.get(where={"source": source}, include=[])
    existing_ids: list[str] = existing.get("ids", [])

    if not existing_ids:
        logger.info("Watcher delete: no chunks found for source=%s", source)
        return DeleteResult(file=source, deleted=0)

    collection.delete(ids=existing_ids)
    if _property_index is not None:
        for chunk_id in existing_ids:
            await _property_index.remove_chunk(chunk_id)
        await _property_index.fts.remove_batch(existing_ids)

    logger.info(
        "Watcher delete complete: source=%s deleted=%d", source, len(existing_ids)
    )
    if _event_bus is not None:
        await _event_bus.publish_async_nowait(
            rag_file_deleted(file=source, deleted=len(existing_ids))
        )
    return DeleteResult(file=source, deleted=len(existing_ids))


async def _index_file_impl(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None,
    chunk_tokens: int | None,
    source: str,
    *,
    force: bool = False,
) -> IndexResult:
    """Inner implementation of file indexing (called with lock held).

    ∀ exit path (unchanged, duplicate, empty, success, error):
    clear_pending is called in the outer finally once mark_pending has been called.
    mark_pending is called before any early-return so the pending journal covers
    all in-flight files, enabling full auto-reconciliation on restart.
    """
    start = time.monotonic()
    is_html_file = file_path.suffix.lower() in {".html", ".htm"}
    raw = file_path.read_bytes()
    # Indexing must run after config load; else schema_version=0 can cause inconsistent hashing.
    if _config is None:
        raise RuntimeError("RAG service configuration not loaded.")
    schema_version = _config.knowledge_extraction.schema_version
    content_hash = file_hash(raw, schema_version=schema_version)
    prefix = content_hash[:16]

    if _registry is not None:
        entry = get_article_entry(_registry, source)
        if entry and entry.content_hash:
            file_sha = hashlib.sha256(raw).hexdigest()
            if file_sha != entry.content_hash:
                logger.warning(
                    "Article registry content_hash mismatch for %s: expected %s, got %s",
                    source,
                    entry.content_hash,
                    file_sha,
                )
                if _event_bus is not None:
                    await _event_bus.publish_async_nowait(
                        rag_article_content_hash_mismatch(
                            file=source,
                            expected_hash=entry.content_hash,
                            actual_hash=file_sha,
                        )
                    )

    collection = _get_collection()

    if file_path.suffix.lower() == ".pdf":
        dup_result = check_pdf_duplicate(collection, content_hash, source)
        if dup_result is not None:
            if dup_result.duplicate_of is not None:
                logger.info(
                    "PDF duplicate detected: %s is duplicate of %s",
                    source,
                    dup_result.duplicate_of,
                )
            if _event_bus is not None:
                await _event_bus.publish_async_nowait(
                    rag_file_skipped(file=source, reason="duplicate_pdf")
                )
            return dup_result

    existing = collection.get(where={"source": source}, include=["metadatas"])
    existing_ids: list[str] = existing.get("ids", [])

    # Register in pending journal before any early-return so the startup reconciler
    # can recover this file if the service is killed at any point after this line.
    # ∀ exit path below: finally: clear_pending removes the entry only if we marked.
    prop_index = _property_index
    pending_marked = False
    if prop_index is None and _event_bus is not None:
        await _event_bus.publish_async_nowait(rag_property_index_unavailable(file=source))
    if prop_index is not None:
        await prop_index.mark_pending(source)
        pending_marked = True

    try:
        if not force and existing_ids and all_ids_match_prefix(existing_ids, prefix):
            # Chunks are current — check if extraction metadata is also present.
            # Missing extraction_schema_version means extraction timed out on a prior run;
            # mismatch or missing extraction_model triggers re-extraction when config sets it.
            existing_metadatas = [
                m for m in (existing.get("metadatas") or []) if isinstance(m, dict)
            ]
            if _config is not None and prop_index is not None:
                expected_model = _config.knowledge_extraction.extraction_model
                has_model_mismatch = bool(expected_model) and any(
                    m.get("extraction_model") != expected_model
                    for m in existing_metadatas
                )
                if has_model_mismatch and _event_bus is not None:
                    await _event_bus.publish_async_nowait(
                        rag_extraction_model_mismatch(
                            file=source,
                            expected_model=expected_model,
                            chunk_count=len(existing_ids),
                        )
                    )
                scope = _config.get_scope_for_path(source)
                ext_result = await recover_missing_extraction(
                    collection=collection,
                    source=source,
                    existing_ids=existing_ids,
                    existing_metadatas=existing_metadatas,
                    config=_config.knowledge_extraction,
                    property_index=prop_index,
                    event_bus=_event_bus,
                    scope=scope,
                )
                if ext_result is not None and ext_result.success:
                    await _maybe_update_corpus_hints()
                    if _event_bus is not None:
                        await _event_bus.publish_async_nowait(
                            rag_file_indexed(
                                file=source,
                                deleted=0,
                                indexed=0,
                                duration_seconds=time.monotonic() - start,
                                document_metadata=(
                                    _article_event_kwargs(_registry, source)
                                    if _registry is not None
                                    else None
                                ),
                                processing_seconds=getattr(
                                    ext_result, "processing_seconds", None
                                ),
                                queue_wait_seconds=getattr(
                                    ext_result, "queue_wait_seconds", None
                                ),
                            )
                        )
                    return IndexResult(
                        deleted=0,
                        indexed=0,
                        unchanged=False,
                        file=source,
                        extraction_entities=ext_result.entities,
                        extraction_topics=ext_result.topics,
                    )
            if _event_bus is not None:
                await _event_bus.publish_async_nowait(
                    rag_file_skipped(file=source, reason="unchanged")
                )
            return IndexResult(deleted=0, indexed=0, unchanged=True, file=source)

        existing_timestamps: dict[str, str] = {}
        for meta in existing.get("metadatas") or []:
            if isinstance(meta, dict):
                chunk_hash = meta.get("chunk_hash")
                indexed_at = meta.get("indexed_at")
                if isinstance(chunk_hash, str) and isinstance(indexed_at, str):
                    existing_timestamps[chunk_hash] = indexed_at

        target_chars = chunk_tokens * _CHARS_PER_TOKEN if chunk_tokens else None
        if is_html_file and _event_bus is not None:
            await _event_bus.publish_async_nowait(
                rag_html_normalization_started(file=source)
            )
        try:
            chunks: list[Chunk] = chunk_file(file_path, target_chars=target_chars)
        except Exception as exc:
            if is_html_file and _event_bus is not None:
                await _event_bus.publish_async_nowait(
                    rag_html_normalization_failed(file=source, error=str(exc))
                )
            raise
        if is_html_file and _event_bus is not None:
            total_chars = sum(len(c.text) for c in chunks)
            await _event_bus.publish_async_nowait(
                rag_html_normalization_completed(file=source, output_chars=total_chars)
            )
        if not chunks:
            if existing_ids:
                # Property index cleanup before ChromaDB delete (same order as main path).
                if prop_index is not None:
                    for old_id in existing_ids:
                        await prop_index.remove_chunk(old_id)
                    await prop_index.fts.remove_batch(existing_ids)
                collection.delete(ids=existing_ids)
            logger.info(
                "Index complete: file=%s deleted=%d indexed=0",
                source,
                len(existing_ids),
            )
            if _event_bus is not None:
                await _event_bus.publish_async_nowait(
                    rag_file_deleted(file=source, deleted=len(existing_ids))
                )
            return IndexResult(
                deleted=len(existing_ids), indexed=0, unchanged=False, file=source
            )

        texts = [c.text for c in chunks]
        metadatas = [c.metadata for c in chunks]
        ids = [f"{prefix}-{i}" for i in range(len(chunks))]

        merged: dict[str, str | int | float | bool] = {}
        if _registry is not None:
            entry_meta = lookup_article_metadata(_registry, source)
            if entry_meta is not None:
                merged.update(entry_meta)
        if metadata_overrides is not None:
            merged.update(metadata_overrides)
        for metadata in metadatas:
            metadata.update(merged)

        now = datetime.now(UTC).isoformat()
        for metadata, chunk in zip(metadatas, chunks, strict=True):
            chunk_hash = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
            metadata["chunk_hash"] = chunk_hash
            metadata["indexed_at"] = existing_timestamps.get(chunk_hash, now)

        if file_path.suffix.lower() == ".pdf":
            for metadata in metadatas:
                metadata["pdf_hash"] = content_hash

        # Bibliography tagging for downstream filter_corpus_hints and retrieve_assemble
        for metadata, chunk in zip(metadatas, chunks, strict=True):
            metadata["is_bibliography"] = chunk_is_junk(chunk.text)

        extraction_entities = 0
        extraction_topics = 0
        extraction_property_entries: list[tuple[str, str, str, str]] = []
        file_batch_start_ts: str | None = None
        ext_result: ExtractionResult | None = None
        if _config is not None and prop_index is not None:
            scope = _config.get_scope_for_path(source)
            ext_result = await run_extraction(
                file=source,
                ids=ids,
                chunks=chunks,
                metadatas=metadatas,
                config=_config.knowledge_extraction,
                property_index=prop_index,
                event_bus=_event_bus,
                apply_property_index=False,
                scope=scope,
            )
            extraction_entities = ext_result.entities
            extraction_topics = ext_result.topics
            extraction_property_entries = ext_result.property_entries
            ext_batch_start = getattr(ext_result, "batch_start_ts", None)
            file_batch_start_ts = (
                ext_batch_start if isinstance(ext_batch_start, str) else None
            )

            bibliography_ids = {
                ids[i]
                for i, meta in enumerate(metadatas)
                if meta.get("is_bibliography")
            }
            if bibliography_ids:
                extraction_property_entries = [
                    e
                    for e in extraction_property_entries
                    if e[1] not in bibliography_ids
                ]

            # ∀ file: extraction failed below threshold ⟹ skip embed+upsert so
            # partially-extracted docs are never queryable. Old chunks (if any)
            # remain intact; next re-index retries extraction.
            if not ext_result.success:
                if _event_bus is not None:
                    await _event_bus.publish_async_nowait(
                        rag_file_indexing_failed(
                            file=source,
                            error="extraction failed below threshold — document excluded until re-indexed",
                        )
                    )
                return IndexResult(deleted=0, indexed=0, unchanged=False, file=source)

        # Contextual embeddings: prepend LLM-generated context for embedding only.
        embed_texts = texts
        if _config is not None and _config.contextualize_model:
            contexts = await contextualize_chunks(
                chunks,
                source,
                _config.contextualize_model,
            )
            if _event_bus is not None:
                await _event_bus.publish_async_nowait(
                    rag_contextualization_applied(
                        file=source,
                        chunk_count=len(contexts),
                        model=_config.contextualize_model,
                    )
                )
            embed_texts = [
                f"{ctx}\n\n{text}" if ctx else text
                for ctx, text in zip(contexts, texts, strict=True)
            ]
            for i, ctx in enumerate(contexts):
                if ctx:
                    metadatas[i]["context_prefix"] = ctx
                    metadatas[i]["contextualize_model"] = _config.contextualize_model

        # Embed before mutating: if embed raises, old chunks remain intact.
        embeddings = await embed_chunks(embed_texts)
        collection.upsert(
            ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas
        )
        if prop_index is not None:
            await prop_index.fts.insert_batch(
                [(cid, source, text) for cid, text in zip(ids, texts, strict=True)]
            )
        if prop_index is not None and extraction_property_entries:
            try:
                await prop_index.add_batch_with_scope(extraction_property_entries)
            except Exception:
                # Compensation: keep old chunks untouched by rolling back new upsert.
                collection.delete(ids=ids)
                await prop_index.fts.remove_batch(ids)
                raise
        # ∀ id ∈ existing_ids ∩ ids: already overwritten by upsert — do not re-delete.
        new_id_set = set(ids)
        stale_ids = list(set(existing_ids) - new_id_set)
        if stale_ids:
            # Property index + FTS cleanup BEFORE ChromaDB delete.
            if prop_index is not None:
                for old_id in stale_ids:
                    await prop_index.remove_chunk(old_id)
                await prop_index.fts.remove_batch(stale_ids)
            collection.delete(ids=stale_ids)
    except Exception as exc:
        if _event_bus is not None:
            await _event_bus.publish_async_nowait(
                rag_file_indexing_failed(
                    file=source,
                    error=f"{type(exc).__qualname__}: {exc}"
                    if str(exc)
                    else type(exc).__qualname__,
                )
            )
        raise
    finally:
        if pending_marked and prop_index is not None:
            await prop_index.clear_pending(source)
        elif pending_marked and prop_index is None:
            logger.error(
                "Property index unavailable while clearing pending for source=%s",
                source,
            )

    await _maybe_update_corpus_hints()
    logger.info(
        "Index complete: file=%s deleted=%d indexed=%d",
        source,
        len(stale_ids),
        len(chunks),
    )
    if _event_bus is not None:
        n_bib = sum(1 for m in metadatas if m.get("is_bibliography"))
        await _event_bus.publish_async_nowait(
            rag_file_indexed(
                file=source,
                deleted=len(stale_ids),
                indexed=len(chunks),
                duration_seconds=time.monotonic() - start,
                batch_start_ts=file_batch_start_ts,
                bibliography_chunks=n_bib,
                document_metadata=(
                    _article_event_kwargs(_registry, source)
                    if _registry is not None
                    else None
                ),
                processing_seconds=getattr(ext_result, "processing_seconds", None),
                queue_wait_seconds=getattr(ext_result, "queue_wait_seconds", None),
            )
        )
    return IndexResult(
        deleted=len(stale_ids),
        indexed=len(chunks),
        unchanged=False,
        file=source,
        extraction_entities=extraction_entities,
        extraction_topics=extraction_topics,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    """Execute hybrid vector+property retrieval over indexed RAG chunks.

    Raises 503 when post-index enrichment is stale (enforcement mode) or when
    embedding retries are exhausted. Raises 400 when requested scope is invalid.
    """
    if _post_index_stale:
        raise HTTPException(
            status_code=503,
            detail="Post-index enrichment stale. Run: tasks/runbooks/rag-post-index-refresh.md",
        )
    original_scope = request.scope
    try:
        request = resolve_scope_request(request, _config)
    except HTTPException as exc:
        if (
            _event_bus is not None
            and original_scope is not None
            and exc.status_code == 400
        ):
            available_scopes = sorted(_config.scopes) if _config is not None else []
            await _event_bus.publish_async_nowait(
                rag_scope_rejected(
                    scope=original_scope,
                    reason="validation_error",
                    available=available_scopes,
                )
            )
        raise

    if _event_bus is not None and original_scope is not None:
        resolved_prefixes = request.source_prefixes or []
        _event_bus.publish_async_nowait(
            rag_scope_resolved(
                scope=original_scope, prefix_count=len(resolved_prefixes)
            )
        )

    collection = _get_collection()
    try:
        query_embedding = await embed_query(request.query, scope=request.scope)
    except EmbeddingTransientError as exc:
        if _event_bus is not None:
            _event_bus.publish_async_nowait(
                rag_search_embedding_failed(
                    model_id=exc.model_id,
                    attempts=exc.attempts,
                    last_status=exc.last_status,
                    query_len=len(request.query),
                    scope=request.scope,
                )
            )
        raise HTTPException(
            status_code=503,
            detail=f"Embedding model temporarily unavailable after {exc.attempts} "
            f"attempts (model={exc.model_id}, last_status={exc.last_status})",
        )

    # ChromaDB >=1.0 dropped $regex on metadata and $contains is array-only,
    # so source_prefixes filtering is done in Python after the query.
    fetch_k = request.top_k * (5 if request.source_prefixes else 3)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=fetch_k,
        include=["documents", "metadatas", "distances"],
    )

    result_ids: list[str] = results["ids"][0] if results["ids"] else []
    chunks: list[str] = results["documents"][0] if results["documents"] else []
    metadatas: list[dict[str, str | int | float | bool]] = (
        results["metadatas"][0] if results["metadatas"] else []
    )
    distances: list[float] = results["distances"][0] if results["distances"] else []

    # Drop bibliography/junk chunks before top-k truncation so useful content
    # fills the budget. Over-fetching provides headroom.
    if result_ids:
        clean = [
            (rid, doc, meta, dist)
            for rid, doc, meta, dist in zip(
                result_ids, chunks, metadatas, distances, strict=True
            )
            if not meta.get("is_bibliography")
        ]
        if clean:
            result_ids = [t[0] for t in clean]
            chunks = [t[1] for t in clean]
            metadatas = [t[2] for t in clean]
            distances = [t[3] for t in clean]

    result_ids, chunks, metadatas, distances = apply_source_prefix_filter_with_ids(
        ids=result_ids,
        chunks=chunks,
        metadatas=metadatas,
        distances=distances,
        source_prefixes=request.source_prefixes,
        top_k=request.top_k,
    )

    bm25_hits = 0
    if _property_index is not None:
        result_ids, chunks, metadatas, distances, bm25_hits = apply_bm25_sidecar(
            ids=result_ids,
            chunks=chunks,
            metadatas=metadatas,
            distances=distances,
            query=request.query,
            fts=_property_index.fts,
            collection=collection,
            source_prefixes=request.source_prefixes,
        )

    property_hits = 0
    if _property_index is not None and _config is not None:
        result_ids, chunks, metadatas, distances, property_hits = apply_property_boost(
            ids=result_ids,
            chunks=chunks,
            metadatas=metadatas,
            distances=distances,
            query=request.query,
            property_index=_property_index,
            boost_factor=_config.knowledge_extraction.property_boost_factor,
        )

    chunks, metadatas, distances = apply_max_distance_filter(
        chunks=chunks,
        metadatas=metadatas,
        distances=distances,
        max_distance=request.max_distance,
    )
    chunks, metadatas, distances = apply_recency_sort(
        chunks=chunks,
        metadatas=metadatas,
        distances=distances,
        recency_weight=request.recency_weight,
    )

    if _event_bus is not None:
        result_count = len(chunks)
        event = (
            rag_search_no_results(query_len=len(request.query), scope=request.scope)
            if result_count == 0
            else rag_search_executed(
                query_len=len(request.query),
                top_k=request.top_k,
                results=result_count,
                scope=request.scope,
            )
        )
        _event_bus.publish_async_nowait(event)

    return SearchResponse(
        chunks=chunks,
        metadata=metadatas,
        distances=distances,
        property_hits=property_hits,
    )


@app.post("/chunks_by_index", response_model=ChunksByIndexResponse)
async def chunks_by_index(request: ChunksByIndexRequest) -> ChunksByIndexResponse:
    """Fetch specific chunks by source + chunk_index for neighbor expansion.

    Uses collection.get() with metadata where clause — no embedding needed.
    Batched by source for efficiency.
    """
    collection = _get_collection()
    results: list[ChunkByIndexItem] = []

    for group in request.groups:
        if not group.chunk_indices:
            continue
        where_filter: dict[str, object]
        # Assuming ChromaDB supports $in for integer metadata. If not, the original
        # structure is necessary, but could be slightly cleaner.
        where_filter = {
            "$and": [
                {"source": group.source},
                {"chunk_index": {"$in": group.chunk_indices}},
            ]
        }
        try:
            raw = collection.get(
                where=where_filter,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.error(
                "chunks_by_index: failed for source=%s: %s",
                group.source,
                e,
                exc_info=True,
            )
            raise

        ids = raw.get("ids") or []
        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        requested_set = set(group.chunk_indices)
        for chunk_id, doc, meta in zip(ids, docs, metas, strict=True):
            idx = meta.get("chunk_index")
            if idx is not None and int(idx) in requested_set:
                results.append(
                    ChunkByIndexItem(
                        chunk_id=chunk_id,
                        source=group.source,
                        chunk_index=int(idx),
                        text=doc or "",
                        metadata=meta,
                    )
                )

    return ChunksByIndexResponse(chunks=results)


@app.get("/scopes", response_model=ScopesResponse)
async def get_scopes() -> ScopesResponse:
    loaded_config = require_loaded_config(_config)
    if _event_bus is not None:
        await _event_bus.publish_async_nowait(
            rag_scopes_listed(count=len(loaded_config.scopes))
        )
    return ScopesResponse(
        scopes={
            name: ScopeInfo(prefixes=scope.prefixes, description=scope.description)
            for name, scope in loaded_config.scopes.items()
        },
    )


@app.get("/extraction/failed", response_model=FailedExtractionResponse)
def get_failed_extractions(source: str | None = None) -> FailedExtractionResponse:
    """Return chunks whose extraction failed, optionally filtered by source file path."""
    if _property_index is None:
        return FailedExtractionResponse(total=0, chunks=[])
    records = _property_index.get_failed_chunks(source=source)
    return FailedExtractionResponse(
        total=len(records),
        chunks=[
            FailedChunkItem(
                chunk_id=r.chunk_id,
                source=r.source,
                error=r.error,
                attempt_count=r.attempt_count,
                recorded_at=r.recorded_at,
            )
            for r in records
        ],
    )


def _set_collection(col: chromadb.Collection) -> None:
    """Set global ChromaDB collection instance.

    Args:
        col: ChromaDB collection object.

    This exists for admin route wiring and tests that swap in an isolated
    collection without re-running full application startup.
    """
    global _collection
    _collection = col


_admin_router = register_admin_routes(
    index_file_fn=_index_file,
    get_collection_fn=_get_collection,
    get_watcher_manager_fn=lambda: _watcher_manager,
    get_chroma_fn=lambda: _chroma,
    set_collection_fn=_set_collection,
    collection_name=COLLECTION_NAME,
    get_property_index_fn=lambda: _property_index,
    get_event_bus_fn=lambda: _event_bus,
)
app.include_router(_admin_router)
