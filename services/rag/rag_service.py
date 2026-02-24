from __future__ import annotations

import hashlib
import logging
import math
from datetime import UTC, datetime
from pathlib import Path

import chromadb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from services.rag.chunkers import Chunk, chunk_file
from services.rag.embeddings import embed_chunks, embed_query

logger = logging.getLogger(__name__)

COLLECTION_NAME = "knowledge"
DEFAULT_EXTENSIONS = [".md", ".mdc", ".txt", ".pdf", ".py", ".js", ".ts"]

app = FastAPI(title="RAG Service")

_chroma: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None


def _get_collection() -> chromadb.Collection:
    assert _collection is not None, "Collection not initialized"
    return _collection


@app.on_event("startup")
def _startup() -> None:
    global _chroma, _collection
    store_path = Path.home() / ".rag" / "store"
    store_path.mkdir(parents=True, exist_ok=True)
    _chroma = chromadb.PersistentClient(path=str(store_path))
    _collection = _chroma.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


DECAY_LAMBDA = 0.01  # half-life ≈ 69 days


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    recency_weight: float = 0.0
    max_distance: float | None = None  # None = return all (backward compat)


class SearchResponse(BaseModel):
    chunks: list[str]
    metadata: list[dict[str, str]]
    distances: list[float]


class IndexRequest(BaseModel):
    path: str


class IndexResponse(BaseModel):
    indexed: int
    skipped: int
    file: str


class IndexDirectoryRequest(BaseModel):
    path: str
    extensions: list[str] | None = None


class IndexDirectoryResponse(BaseModel):
    indexed: int
    skipped: int
    files: int


class StatsResponse(BaseModel):
    count: int
    collection: str


class ClearResponse(BaseModel):
    deleted: int
    collection: str


class SourceResponse(BaseModel):
    chunks: list[str]
    metadata: list[dict[str, str]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _index_file(file_path: Path) -> tuple[int, int]:
    """Index a single file. Returns (indexed, skipped)."""
    raw = file_path.read_bytes()
    prefix = _file_hash(raw)[:16]

    chunks: list[Chunk] = chunk_file(file_path)
    if not chunks:
        return 0, 0

    collection = _get_collection()

    ids = [f"{prefix}-{i}" for i in range(len(chunks))]
    texts = [c.text for c in chunks]
    metadatas = [c.metadata for c in chunks]

    # Check which IDs already exist (idempotency)
    existing = collection.get(ids=ids, include=[])
    existing_ids: set[str] = set(existing["ids"])

    new_indices = [i for i, id_ in enumerate(ids) if id_ not in existing_ids]
    if not new_indices:
        return 0, len(chunks)

    new_texts = [texts[i] for i in new_indices]
    new_ids = [ids[i] for i in new_indices]
    new_metadatas = [metadatas[i] for i in new_indices]

    now = datetime.now(UTC).isoformat()
    for m in new_metadatas:
        m["indexed_at"] = now

    embeddings = await embed_chunks(new_texts)

    collection.upsert(
        ids=new_ids,
        embeddings=embeddings,
        documents=new_texts,
        metadatas=new_metadatas,
    )

    return len(new_indices), len(chunks) - len(new_indices)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _apply_recency(
    distances: list[float],
    metadatas: list[dict],
    recency_weight: float,
) -> list[float]:
    """Re-score distances with optional recency decay.

    ChromaDB distances are cosine distances (0 = identical, 2 = opposite).
    Lower is better. Recency bonus subtracts from distance to boost recent items.

    INV: recency_weight = 0.0 ⟹ distances unchanged
    INV: ∀ chunk without indexed_at: recency_bonus = 0.0 (no penalty, no boost)
    """
    if recency_weight <= 0.0:
        return distances

    now = datetime.now(UTC)
    result: list[float] = []
    for dist, meta in zip(distances, metadatas, strict=True):
        indexed_at_str = meta.get("indexed_at")
        if not indexed_at_str:
            result.append(dist)
            continue
        indexed_at = datetime.fromisoformat(indexed_at_str)
        days_old = max((now - indexed_at).total_seconds() / 86400, 0.0)
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

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=request.top_k,
        include=["documents", "metadatas", "distances"],
    )

    chunks: list[str] = results["documents"][0] if results["documents"] else []
    metadatas: list[dict] = results["metadatas"][0] if results["metadatas"] else []
    distances: list[float] = results["distances"][0] if results["distances"] else []

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


@app.post("/index", response_model=IndexResponse)
async def index_file(request: IndexRequest) -> IndexResponse:
    file_path = Path(request.path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {request.path}")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {request.path}")

    indexed, skipped = await _index_file(file_path)
    return IndexResponse(indexed=indexed, skipped=skipped, file=str(file_path))


@app.post("/index_directory", response_model=IndexDirectoryResponse)
async def index_directory(request: IndexDirectoryRequest) -> IndexDirectoryResponse:
    dir_path = Path(request.path)
    if not dir_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Directory not found: {request.path}"
        )
    if not dir_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {request.path}")

    extensions = set(request.extensions or DEFAULT_EXTENSIONS)
    total_indexed = 0
    total_skipped = 0
    file_count = 0

    for root, _dirs, files in dir_path.walk():
        for name in files:
            file_path = root / name
            if file_path.suffix.lower() not in extensions:
                continue
            try:
                indexed, skipped = await _index_file(file_path)
                total_indexed += indexed
                total_skipped += skipped
                file_count += 1
            except Exception as e:
                logger.warning("Skipping %s: %s", file_path, e)

    return IndexDirectoryResponse(
        indexed=total_indexed,
        skipped=total_skipped,
        files=file_count,
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
    pairs = sorted(
        zip(results["documents"], results["metadatas"]),
        key=lambda p: p[1].get("chunk_index", 0),
    )
    docs, metas = zip(*pairs) if pairs else ([], [])
    return SourceResponse(chunks=list(docs), metadata=list(metas))


@app.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    collection = _get_collection()
    return StatsResponse(count=collection.count(), collection=COLLECTION_NAME)


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
