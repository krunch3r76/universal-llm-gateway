"""ChromaDB vector store for cortex assertion embeddings.

Stores assertion embeddings alongside the SQLite DB in a sibling `chroma/`
directory. Provides upsert, search, and delete operations for the hybrid
search pipeline (B2: CombMAX score fusion).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import chromadb

logger = logging.getLogger("cortex-api.vector_store")

_client: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None


def init_vector_store(db_dir: Path) -> None:
    """Create PersistentClient + get_or_create the cortex_assertions collection.

    Args:
        db_dir: Directory containing cortex.db. ChromaDB store is created
                as a sibling ``chroma/`` directory.
    """
    global _client, _collection
    chroma_path = db_dir / "chroma"
    chroma_path.mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=str(chroma_path))
    _collection = _client.get_or_create_collection(
        name="cortex_assertions",
        metadata={"hnsw:space": "cosine"},
    )
    count = _collection.count()
    logger.info("Vector store initialized: %s (%d embeddings)", chroma_path, count)


def is_initialized() -> bool:
    """Return True if the vector store has been initialized."""
    return _collection is not None


def _require_init() -> None:
    if _collection is None:
        raise RuntimeError(
            "Vector store not initialized — call init_vector_store() at startup"
        )


def assertion_embedding_text(assertion: dict) -> str:
    """Build composite text for embedding an assertion.

    Combines claim + prospective_summary + flattened events for maximum
    semantic coverage.
    """
    parts = [assertion["claim"]]
    if assertion.get("prospective_summary"):
        parts.append(assertion["prospective_summary"])
    if assertion.get("events_json"):
        try:
            events = json.loads(assertion["events_json"])
            for ev in events:
                if ev.get("event"):
                    parts.append(ev["event"])
                if ev.get("consequence"):
                    parts.append(ev["consequence"])
        except (json.JSONDecodeError, TypeError):
            pass
    return " ".join(parts)


def upsert_assertion_embedding(
    assertion_id: int,
    text: str,
    embedding: list[float],
    metadata: dict,
) -> None:
    """Upsert an assertion embedding into the ChromaDB collection."""
    _require_init()
    assert _collection is not None
    _collection.upsert(
        ids=[str(assertion_id)],
        embeddings=[embedding],
        documents=[text],
        metadatas=[metadata],
    )


def search_similar(
    query_embedding: list[float],
    n_results: int = 20,
) -> list[dict]:
    """Search for similar assertions by embedding vector.

    Returns list of dicts with keys: assertion_id, distance, cosine_similarity,
    and stored metadata.
    """
    _require_init()
    assert _collection is not None
    count = _collection.count()
    if count == 0:
        return []
    effective_n = min(n_results, count)
    results = _collection.query(
        query_embeddings=[query_embedding],
        n_results=effective_n,
        include=["distances", "metadatas"],
    )
    items: list[dict] = []
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    for i, doc_id in enumerate(ids):
        distance = distances[i] if i < len(distances) else 1.0
        cosine_sim = max(0.0, 1.0 - distance)
        meta = metadatas[i] if i < len(metadatas) else {}
        items.append(
            {
                "assertion_id": int(doc_id),
                "distance": distance,
                "cosine_similarity": cosine_sim,
                **(meta or {}),
            }
        )
    return items


def delete_assertion_embedding(assertion_id: int) -> None:
    """Remove an assertion's embedding (e.g. on supersession)."""
    _require_init()
    assert _collection is not None
    try:
        _collection.delete(ids=[str(assertion_id)])
    except Exception:
        logger.warning(
            "Failed to delete embedding for assertion %d", assertion_id, exc_info=True
        )


def get_collection_count() -> int:
    """Return the number of embeddings in the collection."""
    if _collection is None:
        return 0
    return _collection.count()
