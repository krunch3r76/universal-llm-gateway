from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import chromadb
from fastapi import FastAPI, HTTPException
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from services.rag.chunkers import Chunk, chunk_file
from services.rag.config import RagConfig, load_config
from services.rag.directory_ops import find_removed_sources, index_directory_contents
from services.rag.embeddings import embed_chunks, embed_query, wait_until_healthy
from services.rag.events import (
    RagScopeRejected,
    RagScopeResolved,
    RagScopesListed,
    RagShutdown,
    RagStarted,
)
from services.rag.indexing_helpers import (
    all_ids_match_prefix,
    check_pdf_duplicate,
    file_hash,
)
from services.rag.models import (
    ClearResponse,
    IndexDirectoryRequest,
    IndexDirectoryResponse,
    IndexRequest,
    IndexResult,
    ScopeInfo,
    ScopesResponse,
    SearchRequest,
    SearchResponse,
    SourceResponse,
    StatsResponse,
)
from services.rag.search_scope import (
    apply_max_distance_filter,
    apply_recency_sort,
    apply_source_prefix_filter,
    require_loaded_config,
    resolve_scope_request,
)
from services.rag.watcher_manager import WatcherManager

logger = logging.getLogger(__name__)

COLLECTION_NAME = "knowledge"
DEFAULT_EXTENSIONS = [".md", ".mdc", ".txt", ".pdf", ".epub", ".py", ".js", ".ts"]

app = FastAPI(title="RAG Service")

_chroma: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None
_watcher_manager: WatcherManager | None = None
_event_bus: EventBus | None = None
_broadcaster: MinimalEventDebugBroadcaster | None = None
_config: RagConfig | None = None
_init_task: asyncio.Task[None] | None = None

# Serialize concurrent indexing of the same file path (watcher + API can race).
_file_index_locks: dict[str, asyncio.Lock] = {}


def _get_collection() -> chromadb.Collection:
    assert _collection is not None, "Collection not initialized"
    return _collection


def get_event_bus() -> EventBus | None:
    return _event_bus


@app.on_event("startup")
async def _startup() -> None:
    global _chroma, _collection, _watcher_manager, _event_bus, _broadcaster, _config, _init_task
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
    await _event_bus.publish_async(RagStarted())
    _config = load_config()
    if _config.watch_directories:
        _init_task = asyncio.create_task(
            _deferred_watcher_start(_config), name="rag-watcher-init"
        )


async def _deferred_watcher_start(config: RagConfig) -> None:
    """Start watchers after embedding endpoint is healthy.

    Runs as a background task so uvicorn binds the UDS socket immediately
    instead of blocking on the (potentially slow) initial reindex.
    """
    global _watcher_manager

    async def _watcher_index_fn(
        path: Path, chunk_tokens: int | None
    ) -> IndexResult:
        return await _index_file(path, chunk_tokens=chunk_tokens)

    _watcher_manager = WatcherManager(
        index_fn=_watcher_index_fn, event_bus=_event_bus
    )
    try:
        await wait_until_healthy()
    except TimeoutError:
        logger.error(
            "Embedding endpoint not healthy after timeout; "
            "initial watcher sweep will be skipped for files requiring embedding"
        )
    await _watcher_manager.start(config)


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _watcher_manager, _event_bus, _broadcaster
    if _event_bus is not None:
        await _event_bus.publish_async(RagShutdown())
    if _broadcaster is not None:
        await _broadcaster.stop_debug_server()
        _broadcaster = None
    _event_bus = None
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


async def _index_file_impl(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None,
    chunk_tokens: int | None,
    source: str,
) -> IndexResult:
    """Inner implementation of file indexing (called with lock held)."""
    raw = file_path.read_bytes()
    content_hash = file_hash(raw)
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
            return dup_result

    existing = collection.get(where={"source": source}, include=["metadatas"])
    existing_ids: list[str] = existing.get("ids", [])

    if all_ids_match_prefix(existing_ids, prefix):
        return IndexResult(deleted=0, indexed=0, unchanged=True, file=source)

    existing_timestamps: dict[str, str] = {}
    for meta in existing.get("metadatas") or []:
        if isinstance(meta, dict):
            chunk_hash = meta.get("chunk_hash")
            indexed_at = meta.get("indexed_at")
            if isinstance(chunk_hash, str) and isinstance(indexed_at, str):
                existing_timestamps[chunk_hash] = indexed_at

    max_chunk_chars = chunk_tokens * _TOKEN_ESTIMATE if chunk_tokens else None
    chunks: list[Chunk] = chunk_file(file_path, max_chunk_chars=max_chunk_chars)
    if not chunks:
        if existing_ids:
            collection.delete(ids=existing_ids)
        logger.info(
            "Index complete: file=%s deleted=%d indexed=0", source, len(existing_ids)
        )
        return IndexResult(
            deleted=len(existing_ids), indexed=0, unchanged=False, file=source
        )

    texts = [c.text for c in chunks]
    metadatas = [c.metadata for c in chunks]
    ids = [f"{prefix}-{i}" for i in range(len(chunks))]

    now = datetime.now(UTC).isoformat()
    for metadata, chunk in zip(metadatas, chunks, strict=True):
        chunk_hash = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
        metadata["chunk_hash"] = chunk_hash
        metadata["indexed_at"] = existing_timestamps.get(chunk_hash, now)

    if file_path.suffix.lower() == ".pdf":
        for metadata in metadatas:
            metadata["pdf_hash"] = content_hash

    if metadata_overrides:
        for metadata in metadatas:
            metadata.update(metadata_overrides)

    # Embed before mutating: if embed raises, old chunks remain intact.
    # ∀ failed embed → zero-chunk state is impossible.
    embeddings = await embed_chunks(texts)
    if existing_ids:
        collection.delete(ids=existing_ids)
    collection.upsert(
        ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas
    )

    logger.info(
        "Index complete: file=%s deleted=%d indexed=%d",
        source,
        len(existing_ids),
        len(chunks),
    )
    return IndexResult(
        deleted=len(existing_ids),
        indexed=len(chunks),
        unchanged=False,
        file=source,
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
            asyncio.create_task(
                _event_bus.publish_async_nowait(
                    RagScopeRejected(
                        scope=original_scope,
                        reason="validation_error",
                        available=available_scopes,
                    )
                )
            )
        raise

    if original_scope is not None and _event_bus is not None:
        resolved_prefixes = request.source_prefixes or []
        asyncio.create_task(
            _event_bus.publish_async_nowait(
                RagScopeResolved(
                    scope=original_scope, prefix_count=len(resolved_prefixes)
                )
            )
        )

    collection = _get_collection()
    query_embedding = await embed_query(request.query)

    # ChromaDB >=1.0 dropped $regex on metadata and $contains is array-only,
    # so source_prefixes filtering is done in Python after the query.
    fetch_k = request.top_k * 5 if request.source_prefixes else request.top_k

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=fetch_k,
        include=["documents", "metadatas", "distances"],
    )

    chunks: list[str] = results["documents"][0] if results["documents"] else []
    metadatas: list[dict[str, str | int | float | bool]] = (
        results["metadatas"][0] if results["metadatas"] else []
    )
    distances: list[float] = results["distances"][0] if results["distances"] else []

    chunks, metadatas, distances = apply_source_prefix_filter(
        chunks=chunks,
        metadatas=metadatas,
        distances=distances,
        source_prefixes=request.source_prefixes,
        top_k=request.top_k,
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

    return SearchResponse(chunks=chunks, metadata=metadatas, distances=distances)


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


@app.post("/index", response_model=IndexResult)
async def index_file(request: IndexRequest) -> IndexResult:
    return await _index_file(
        _validate_file(request.path),
        metadata_overrides=request.metadata_overrides,
    )


@app.post("/index_directory", response_model=IndexDirectoryResponse)
async def index_directory(request: IndexDirectoryRequest) -> IndexDirectoryResponse:
    dir_path = _validate_directory(request.path)
    extensions = set(request.extensions or DEFAULT_EXTENSIONS)
    totals, _walked = await index_directory_contents(
        dir_path=dir_path,
        extensions=extensions,
        index_file=_index_file,
        metadata_overrides=request.metadata_overrides,
        collect_walked_sources=False,
        on_index_error=lambda file_path, exc: logger.warning(
            "Skipping %s: %s", file_path, exc
        ),
    )

    return IndexDirectoryResponse(
        indexed=totals.indexed,
        deleted=totals.deleted,
        unchanged=totals.unchanged,
        files=totals.files,
        duplicates=totals.duplicates,
    )


@app.post("/reindex", response_model=IndexResult)
async def reindex_file(request: IndexRequest) -> IndexResult:
    return await _index_file(
        _validate_file(request.path),
        metadata_overrides=request.metadata_overrides,
    )


@app.post("/reindex_directory", response_model=IndexDirectoryResponse)
async def reindex_directory(request: IndexDirectoryRequest) -> IndexDirectoryResponse:
    """Reindex a directory and remove chunks for deleted source files."""
    dir_path = _validate_directory(request.path)
    extensions = set(request.extensions or DEFAULT_EXTENSIONS)
    totals, walked_sources = await index_directory_contents(
        dir_path=dir_path,
        extensions=extensions,
        index_file=_index_file,
        metadata_overrides=request.metadata_overrides,
        collect_walked_sources=True,
        on_index_error=lambda file_path, exc: logger.warning(
            "Skipping %s: %s", file_path, exc
        ),
    )
    collection = _get_collection()
    removed_sources = find_removed_sources(
        collection=collection, dir_path=dir_path, walked_sources=walked_sources
    )

    for source in removed_sources:
        stale = collection.get(where={"source": source}, include=[])
        stale_ids: list[str] = stale.get("ids", [])
        if stale_ids:
            collection.delete(ids=stale_ids)
            totals.deleted += len(stale_ids)
            logger.info(
                "Removed stale chunks: source=%s deleted=%d", source, len(stale_ids)
            )

    return IndexDirectoryResponse(
        indexed=totals.indexed,
        deleted=totals.deleted,
        unchanged=totals.unchanged,
        files=totals.files,
        duplicates=totals.duplicates,
    )


@app.get("/source", response_model=SourceResponse)
def get_source(path: str) -> SourceResponse:
    """Return all indexed chunks for a specific source file, in order."""
    collection = _get_collection()
    results = collection.get(
        where={"source": path},
        include=["documents", "metadatas"],
    )
    if not results["documents"]:
        raise HTTPException(status_code=404, detail=f"No chunks indexed for: {path}")
    documents = results.get("documents") or []
    metadatas = results.get("metadatas") or []
    pairs = sorted(
        zip(documents, metadatas, strict=False),
        key=lambda pair: pair[1].get("chunk_index", 0),
    )
    docs = [pair[0] for pair in pairs]
    metas = [pair[1] for pair in pairs]
    return SourceResponse(chunks=docs, metadata=metas)


@app.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    collection = _get_collection()
    return StatsResponse(count=collection.count(), collection=COLLECTION_NAME)


@app.get("/watch/status")
def watch_status() -> list[dict[str, str | int | bool]]:
    if _watcher_manager is None:
        return []
    return _watcher_manager.get_status()


@app.get("/scopes", response_model=ScopesResponse)
def get_scopes() -> ScopesResponse:
    loaded_config = require_loaded_config(_config)
    if _event_bus is not None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # FastAPI can execute sync handlers in a worker thread where no
            # event loop exists; scope listing should still succeed.
            logger.debug("Skipping rag.scopes.listed event: no running event loop")
        else:
            loop.create_task(
                _event_bus.publish_async_nowait(
                    RagScopesListed(count=len(loaded_config.scopes))
                )
            )
    return ScopesResponse(
        scopes={
            name: ScopeInfo(prefixes=scope.prefixes, description=scope.description)
            for name, scope in loaded_config.scopes.items()
        },
    )


@app.post("/clear", response_model=ClearResponse)
def clear() -> ClearResponse:
    global _collection
    assert _chroma is not None, "ChromaDB client not initialized"
    collection = _get_collection()
    deleted = collection.count()
    _chroma.delete_collection(COLLECTION_NAME)
    _collection = _chroma.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return ClearResponse(deleted=deleted, collection=COLLECTION_NAME)
