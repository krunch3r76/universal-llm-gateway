from __future__ import annotations

import hashlib
from pathlib import Path

import chromadb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from services.rag.chunkers import Chunk, chunk_file
from services.rag.embeddings import embed_chunks, embed_query

COLLECTION_NAME = "knowledge"
DEFAULT_EXTENSIONS = [".md", ".txt", ".pdf", ".py", ".js", ".ts"]

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


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


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

    embeddings = await embed_chunks(new_texts)

    collection.upsert(
        ids=new_ids,
        embeddings=embeddings,
        documents=new_texts,
        metadatas=new_metadatas,
    )

    return len(new_indices), len(chunks) - len(new_indices)


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

    chunks = results["documents"][0] if results["documents"] else []
    metadatas = results["metadatas"][0] if results["metadatas"] else []
    distances = results["distances"][0] if results["distances"] else []

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
            except (ValueError, Exception):
                # Unsupported extension or read error — skip silently (already filtered)
                pass

    return IndexDirectoryResponse(
        indexed=total_indexed,
        skipped=total_skipped,
        files=file_count,
    )


@app.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    collection = _get_collection()
    return StatsResponse(count=collection.count(), collection=COLLECTION_NAME)
