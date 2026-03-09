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
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path

import chromadb
from fastapi import FastAPI, HTTPException
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from services.rag.admin_routes import register_admin_routes
from services.rag.chunkers import Chunk, chunk_file
from services.rag.config import RagConfig, load_config
from services.rag.embeddings import configure as configure_embeddings
from services.rag.embeddings import embed_chunks, embed_query, wait_until_healthy
from services.rag.events import (
    rag_file_deleted,
    rag_file_indexed,
    rag_file_indexing_failed,
    rag_file_skipped,
    rag_pending_reconciled,
    rag_scope_rejected,
    rag_scope_resolved,
    rag_scopes_listed,
    rag_search_executed,
    rag_search_no_results,
    rag_shutdown,
    rag_started,
)
from services.rag.extraction_wiring import recover_missing_extraction, run_extraction
from services.rag.indexing_helpers import (
    all_ids_match_prefix,
    check_pdf_duplicate,
    file_hash,
)
from services.rag.models import (
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
    apply_max_distance_filter,
    apply_property_boost,
    apply_recency_sort,
    apply_source_prefix_filter_with_ids,
    require_loaded_config,
    resolve_scope_request,
)
from services.rag.watcher_manager import WatcherManager

logger = logging.getLogger(__name__)

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

# Serialize concurrent indexing of the same file path (watcher + API can race).
_file_index_locks: dict[str, asyncio.Lock] = {}


def _get_collection() -> chromadb.Collection:
    """Return initialized ChromaDB collection."""
    assert _collection is not None, "Collection not initialized"
    return _collection


def get_event_bus() -> EventBus | None:
    """Return initialized event bus, if available."""
    return _event_bus


@app.on_event("startup")
async def _startup() -> None:
    global \
        _chroma, \
        _collection, \
        _watcher_manager, \
        _event_bus, \
        _broadcaster, \
        _config, \
        _init_task, \
        _property_index
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
    _property_index = PropertyIndex()
    await _property_index.start()
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

    _watcher_manager = WatcherManager(
        index_fn=_watcher_index_fn,
        delete_fn=_watcher_delete_fn,
        event_bus=_event_bus,
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
        pending_files = _property_index.get_pending_files()
        if pending_files:
            logger.warning(
                "Reconciling %d files pending from interrupted indexing",
                len(pending_files),
            )
            reconciled = cleared = failed_transient = failed_permanent = 0
            for source in pending_files:
                file_path = Path(source)
                if not file_path.exists():
                    await _property_index.clear_pending(source)
                    logger.info("Pending file removed (deleted): %s", source)
                    cleared += 1
                    continue
                try:
                    chunk_tokens = _resolve_chunk_tokens_for_file(file_path, config)
                    await _index_file(file_path, chunk_tokens=chunk_tokens)
                    reconciled += 1
                except (TimeoutError, ConnectionError) as e:
                    logger.warning(
                        "Transient error reconciling %s; will retry on next sweep: %s",
                        source,
                        e,
                    )
                    failed_transient += 1
                except Exception:
                    logger.error(
                        "Permanent error reconciling %s; requires manual intervention",
                        source,
                        exc_info=True,
                    )
                    failed_permanent += 1
            if _event_bus is not None:
                await _event_bus.publish_async(
                    rag_pending_reconciled(
                        reconciled=reconciled,
                        cleared=cleared,
                        failed_transient=failed_transient,
                        failed_permanent=failed_permanent,
                    )
                )

    await _watcher_manager.start(config)


def _resolve_chunk_tokens_for_file(file_path: Path, config: RagConfig) -> int | None:
    """
    Resolve chunk_tokens for a file using the same watch-directory rules.

    This preserves chunking consistency when reconciling pending files after
    interrupted indexing.
    """
    resolved_file = file_path.expanduser().resolve()
    for watch_directory in config.watch_directories:
        watch_path = Path(watch_directory.path).expanduser().resolve()
        if not resolved_file.is_relative_to(watch_path):
            continue
        if not watch_directory.recursive and resolved_file.parent != watch_path:
            continue
        if watch_directory.extensions and (
            resolved_file.suffix.lower()
            not in {f".{ext.lower().lstrip('.')}" for ext in watch_directory.extensions}
        ):
            continue
        if any(fnmatch(resolved_file.name, pat) for pat in watch_directory.exclude):
            continue
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
    if _watcher_manager is not None:
        await _watcher_manager.stop()
        _watcher_manager = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TOKEN_ESTIMATE = 4


async def _index_file(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None = None,
    chunk_tokens: int | None = None,
) -> IndexResult:
    """Index a file, cleaning up stale chunks when content changed."""
    source = str(file_path)
    lock = _file_index_locks.setdefault(source, asyncio.Lock())
    async with lock:
        return await _index_file_impl(
            file_path, metadata_overrides, chunk_tokens, source
        )


async def _delete_file(file_path: Path) -> DeleteResult:
    """Delete all indexed chunks for a removed file under the per-source lock."""
    source = str(file_path)
    lock = _file_index_locks.setdefault(source, asyncio.Lock())
    async with lock:
        return await _delete_file_impl(source)


async def _delete_file_impl(source: str) -> DeleteResult:
    """Inner delete implementation (called with lock held)."""
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
) -> IndexResult:
    """Inner implementation of file indexing (called with lock held)."""
    raw = file_path.read_bytes()
    schema_version = (
        _config.knowledge_extraction.schema_version if _config is not None else 0
    )
    content_hash = file_hash(raw, schema_version=schema_version)
    prefix = content_hash[:16]

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

    if existing_ids and all_ids_match_prefix(existing_ids, prefix):
        # Chunks are current — check if extraction metadata is also present.
        # Missing extraction_schema_version means extraction timed out on a prior run;
        # the file is in ChromaDB but the property index is unpopulated.
        if _config is not None and _property_index is not None:
            ext_result = await recover_missing_extraction(
                collection=collection,
                source=source,
                existing_ids=existing_ids,
                existing_metadatas=[
                    m for m in (existing.get("metadatas") or []) if isinstance(m, dict)
                ],
                config=_config.knowledge_extraction,
                property_index=_property_index,
                event_bus=_event_bus,
            )
            if ext_result is not None and ext_result.success:
                if _event_bus is not None:
                    await _event_bus.publish_async_nowait(
                        rag_file_indexed(file=source, deleted=0, indexed=0)
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

    target_chars = chunk_tokens * _TOKEN_ESTIMATE if chunk_tokens else None
    chunks: list[Chunk] = chunk_file(file_path, target_chars=target_chars)
    if not chunks:
        if existing_ids:
            collection.delete(ids=existing_ids)
        logger.info(
            "Index complete: file=%s deleted=%d indexed=0", source, len(existing_ids)
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

    # Apply caller overrides first; reserved keys below always win.
    if metadata_overrides is not None:
        for metadata in metadatas:
            metadata.update(metadata_overrides)

    now = datetime.now(UTC).isoformat()
    for metadata, chunk in zip(metadatas, chunks, strict=True):
        chunk_hash = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
        metadata["chunk_hash"] = chunk_hash
        metadata["indexed_at"] = existing_timestamps.get(chunk_hash, now)

    if file_path.suffix.lower() == ".pdf":
        for metadata in metadatas:
            metadata["pdf_hash"] = content_hash

    # Mark file as in-flight before any store mutation.
    if _property_index is not None:
        await _property_index.mark_pending(source)

    clear_pending_on_success = False
    try:
        extraction_entities = 0
        extraction_topics = 0
        extraction_property_entries: list[tuple[str, str]] = []
        if _config is not None and _property_index is not None:
            ext_result = await run_extraction(
                file=source,
                ids=ids,
                chunks=chunks,
                metadatas=metadatas,
                config=_config.knowledge_extraction,
                property_index=_property_index,
                event_bus=_event_bus,
                apply_property_index=False,
            )
            extraction_entities = ext_result.entities
            extraction_topics = ext_result.topics
            extraction_property_entries = ext_result.property_entries

        # Embed before mutating: if embed raises, old chunks remain intact.
        embeddings = await embed_chunks(texts)
        collection.upsert(
            ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas
        )
        if _property_index is not None and extraction_property_entries:
            try:
                await _property_index.add_batch(extraction_property_entries)
            except Exception:
                # Compensation: keep old chunks untouched by rolling back new upsert.
                collection.delete(ids=ids)
                raise
        if existing_ids:
            collection.delete(ids=existing_ids)
            if _property_index is not None:
                for old_id in existing_ids:
                    await _property_index.remove_chunk(old_id)
        clear_pending_on_success = True
    except Exception as exc:
        if _event_bus is not None:
            await _event_bus.publish_async_nowait(
                rag_file_indexing_failed(file=source, error=str(exc))
            )
        raise

    if clear_pending_on_success and _property_index is not None:
        await _property_index.clear_pending(source)

    logger.info(
        "Index complete: file=%s deleted=%d indexed=%d",
        source,
        len(existing_ids),
        len(chunks),
    )
    if _event_bus is not None:
        await _event_bus.publish_async_nowait(
            rag_file_indexed(
                file=source,
                deleted=len(existing_ids),
                indexed=len(chunks),
            )
        )
    return IndexResult(
        deleted=len(existing_ids),
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

    if original_scope is not None and _event_bus is not None:
        resolved_prefixes = request.source_prefixes or []
        asyncio.create_task(
            _event_bus.publish_async_nowait(
                rag_scope_resolved(
                    scope=original_scope, prefix_count=len(resolved_prefixes)
                )
            )
        )

    collection = _get_collection()
    query_embedding = await embed_query(request.query, scope=request.scope)

    # ChromaDB >=1.0 dropped $regex on metadata and $contains is array-only,
    # so source_prefixes filtering is done in Python after the query.
    fetch_k = request.top_k * 5 if request.source_prefixes else request.top_k

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

    result_ids, chunks, metadatas, distances = apply_source_prefix_filter_with_ids(
        ids=result_ids,
        chunks=chunks,
        metadatas=metadatas,
        distances=distances,
        source_prefixes=request.source_prefixes,
        top_k=request.top_k,
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
        if result_count == 0:
            asyncio.create_task(
                _event_bus.publish_async_nowait(
                    rag_search_no_results(
                        query_len=len(request.query),
                        scope=request.scope,
                    )
                )
            )
        else:
            asyncio.create_task(
                _event_bus.publish_async_nowait(
                    rag_search_executed(
                        query_len=len(request.query),
                        top_k=request.top_k,
                        results=result_count,
                        scope=request.scope,
                    )
                )
            )

    return SearchResponse(
        chunks=chunks,
        metadata=metadatas,
        distances=distances,
        property_hits=property_hits,
    )


@app.get("/scopes", response_model=ScopesResponse)
def get_scopes() -> ScopesResponse:
    loaded_config = require_loaded_config(_config)
    if _event_bus is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                _event_bus.publish_async_nowait(
                    rag_scopes_listed(count=len(loaded_config.scopes))
                )
            )
        except RuntimeError:
            logger.debug("Skipping rag.scopes.listed event: no running event loop")
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
    """Set global ChromaDB collection instance."""
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
)
app.include_router(_admin_router)
