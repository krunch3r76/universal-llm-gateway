from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from datetime import UTC, datetime
from pathlib import Path

import chromadb
from fastapi import FastAPI, HTTPException
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from services.rag.chunkers import Chunk, chunk_file
from services.rag.config import load_config
from services.rag.embeddings import embed_chunks, embed_query, wait_until_healthy
from services.rag.events import RagShutdown, RagStarted
from services.rag.models import (
    DECAY_LAMBDA,
    ClearResponse,
    IndexDirectoryRequest,
    IndexDirectoryResponse,
    IndexRequest,
    IndexResult,
    SearchRequest,
    SearchResponse,
    SourceResponse,
    StatsResponse,
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

# Serialize concurrent indexing of the same file path (watcher + API can race).
_file_index_locks: dict[str, asyncio.Lock] = {}


def _get_collection() -> chromadb.Collection:
    assert _collection is not None, "Collection not initialized"
    return _collection


def get_event_bus() -> EventBus | None:
    return _event_bus


@app.on_event("startup")
async def _startup() -> None:
    global _chroma, _collection, _watcher_manager, _event_bus, _broadcaster
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
    config = load_config()
    if config.watch_directories:

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


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _all_ids_match_prefix(ids: list[str], prefix: str) -> bool:
    return bool(ids) and all(id_.startswith(f"{prefix}-") for id_ in ids)


def _matches_source_prefix(source: str, prefixes: list[str]) -> bool:
    """Check if a chunk's source path starts with any of the given prefixes."""
    return any(source.startswith(prefix) for prefix in prefixes)


def _check_pdf_duplicate(
    collection: chromadb.Collection,
    pdf_hash: str,
    source: str,
) -> IndexResult | None:
    """Return IndexResult if pdf_hash already exists under a different source path."""
    try:
        existing = collection.get(
            where={"pdf_hash": pdf_hash},
            include=["metadatas"],
            limit=1,
        )
    except Exception:
        return None
    for meta in existing.get("metadatas") or []:
        if isinstance(meta, dict):
            existing_source = meta.get("source")
            if isinstance(existing_source, str) and existing_source != source:
                logger.info(
                    "PDF duplicate detected: %s is duplicate of %s",
                    source,
                    existing_source,
                )
                return IndexResult(
                    deleted=0,
                    indexed=0,
                    unchanged=True,
                    file=source,
                    duplicate=True,
                    duplicate_of=existing_source,
                )
    return None


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
    content_hash = _file_hash(raw)
    prefix = content_hash[:16]

    collection = _get_collection()

    if file_path.suffix.lower() == ".pdf":
        dup_result = _check_pdf_duplicate(collection, content_hash, source)
        if dup_result is not None:
            return dup_result

    existing = collection.get(where={"source": source}, include=["metadatas"])
    existing_ids: list[str] = existing.get("ids", [])

    if _all_ids_match_prefix(existing_ids, prefix):
        return IndexResult(deleted=0, indexed=0, unchanged=True, file=source)

    existing_timestamps: dict[str, str] = {}
    for meta in existing.get("metadatas") or []:
        if isinstance(meta, dict):
            chunk_hash = meta.get("chunk_hash")
            indexed_at = meta.get("indexed_at")
            if isinstance(chunk_hash, str) and isinstance(indexed_at, str):
                existing_timestamps[chunk_hash] = indexed_at

    if existing_ids:
        collection.delete(ids=existing_ids)

    max_chunk_chars = chunk_tokens * _TOKEN_ESTIMATE if chunk_tokens else None
    chunks: list[Chunk] = chunk_file(file_path, max_chunk_chars=max_chunk_chars)
    if not chunks:
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

    embeddings = await embed_chunks(texts)
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
# Scoring helpers
# ---------------------------------------------------------------------------


def _apply_recency(
    distances: list[float],
    metadatas: list[dict[str, str | int | float | bool]],
    recency_weight: float,
) -> list[float]:
    """Re-score distances with optional recency decay.

    ChromaDB distances are cosine distances (0 = identical, 2 = opposite).
    Lower is better. Recency bonus subtracts from distance to boost recent items.

    INV: recency_weight = 0.0 ⟹ distances unchanged
    INV: ∀ chunk without date metadata: recency_bonus = 0.0 (no penalty, no boost)
    INV: published_date takes priority over indexed_at when present
    """
    if recency_weight <= 0.0:
        return distances

    now = datetime.now(UTC)
    result: list[float] = []
    for dist, meta in zip(distances, metadatas, strict=True):
        date_str = meta.get("published_date") or meta.get("indexed_at")
        if not isinstance(date_str, str):
            result.append(dist)
            continue
        try:
            doc_date = datetime.fromisoformat(date_str)
        except ValueError:
            result.append(dist)
            continue
        days_old = max((now - doc_date).total_seconds() / 86400, 0.0)
        recency_score = math.exp(-DECAY_LAMBDA * days_old)
        adjusted = dist * (1 - recency_weight) - recency_weight * recency_score
        result.append(adjusted)
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
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

    if request.source_prefixes:
        filtered = [
            (chunk, meta, dist)
            for chunk, meta, dist in zip(chunks, metadatas, distances, strict=True)
            if _matches_source_prefix(
                str(meta.get("source", "")), request.source_prefixes
            )
        ]
        chunks = [x[0] for x in filtered][: request.top_k]
        metadatas = [x[1] for x in filtered][: request.top_k]
        distances = [x[2] for x in filtered][: request.top_k]

    # max_distance filters on raw cosine distances (scale-independent of recency)
    if request.max_distance is not None:
        filtered = [
            (chunk, meta, dist)
            for chunk, meta, dist in zip(chunks, metadatas, distances, strict=True)
            if dist <= request.max_distance
        ]
        if filtered:
            chunks = [x[0] for x in filtered]
            metadatas = [x[1] for x in filtered]
            distances = [x[2] for x in filtered]
        else:
            chunks, metadatas, distances = [], [], []

    if request.recency_weight > 0.0 and chunks:
        adjusted = _apply_recency(distances, metadatas, request.recency_weight)
        sorted_triples = sorted(
            zip(chunks, metadatas, distances, adjusted, strict=True),
            key=lambda t: t[3],
        )
        chunks = [t[0] for t in sorted_triples]
        metadatas = [t[1] for t in sorted_triples]
        distances = [t[2] for t in sorted_triples]

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
    total_indexed = 0
    total_deleted = 0
    total_unchanged = 0
    total_duplicates = 0
    file_count = 0

    for root, _dirs, files in dir_path.walk():
        for name in files:
            file_path = root / name
            if file_path.suffix.lower() not in extensions:
                continue
            try:
                result = await _index_file(
                    file_path,
                    metadata_overrides=request.metadata_overrides,
                )
                total_indexed += result.indexed
                total_deleted += result.deleted
                if result.duplicate:
                    total_duplicates += 1
                elif result.unchanged:
                    total_unchanged += 1
                file_count += 1
            except Exception as e:
                logger.warning("Skipping %s: %s", file_path, e)

    return IndexDirectoryResponse(
        indexed=total_indexed,
        deleted=total_deleted,
        unchanged=total_unchanged,
        files=file_count,
        duplicates=total_duplicates,
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
    total_indexed = 0
    total_deleted = 0
    total_unchanged = 0
    total_duplicates = 0
    file_count = 0
    walked_sources: set[str] = set()

    for root, _dirs, files in dir_path.walk():
        for name in files:
            file_path = root / name
            if file_path.suffix.lower() not in extensions:
                continue
            walked_sources.add(str(file_path.resolve()))
            try:
                result = await _index_file(
                    file_path,
                    metadata_overrides=request.metadata_overrides,
                )
                total_indexed += result.indexed
                total_deleted += result.deleted
                if result.duplicate:
                    total_duplicates += 1
                elif result.unchanged:
                    total_unchanged += 1
                file_count += 1
            except Exception as e:
                logger.warning("Skipping %s: %s", file_path, e)

    collection = _get_collection()
    all_meta = collection.get(include=["metadatas"])
    metadata_rows = all_meta.get("metadatas") or []
    dir_prefix = f"{dir_path.resolve()}/"

    removed_sources = {
        str(source)
        for row in metadata_rows
        if isinstance(row, dict)
        for source in [row.get("source")]
        if isinstance(source, str)
        and source.startswith(dir_prefix)
        and source not in walked_sources
        and not Path(source).exists()
    }

    for source in removed_sources:
        stale = collection.get(where={"source": source}, include=[])
        stale_ids: list[str] = stale.get("ids", [])
        if stale_ids:
            collection.delete(ids=stale_ids)
            total_deleted += len(stale_ids)
            logger.info(
                "Removed stale chunks: source=%s deleted=%d", source, len(stale_ids)
            )

    return IndexDirectoryResponse(
        indexed=total_indexed,
        deleted=total_deleted,
        unchanged=total_unchanged,
        files=file_count,
        duplicates=total_duplicates,
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
