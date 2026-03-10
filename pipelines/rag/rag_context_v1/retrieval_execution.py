"""RAG retrieval execution and RRF merging."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(slots=True)
class RetrievedChunk:
    """Single chunk from a RAG search result.

    Attributes:
        content: The text content of the chunk.
        source: The original source of the chunk (e.g. file path, URL).
        indexed_at: Timestamp when the chunk was indexed.
        metadata: Full metadata dict from the search response (includes extraction field).
        content_hash: MD5 hash of the content for deduplication across queries.
    """

    content: str
    source: str
    indexed_at: str
    metadata: dict[str, object]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.content_hash = hashlib.md5(
            self.content.encode(), usedforsecurity=False
        ).hexdigest()


def rrf_merge(
    results_per_query: list[list[RetrievedChunk]],
    k: int = 60,
    max_chunks: int = 20,
) -> tuple[list[RetrievedChunk], dict[str, float]]:
    """Reciprocal rank fusion across multiple query result sets.

    RRF score: score(chunk) = Σ 1/(k + rank_i + 1), summed across queries
    where rank_i is the 0-based position in query i's results.

    Cosine distances from different queries are incomparable - RRF uses rank
    order only.

    Returns (merged_chunks, scores_by_hash) where scores_by_hash maps
    content_hash -> RRF score for the returned chunks only.
    """
    scores: dict[str, float] = {}
    chunks: dict[str, RetrievedChunk] = {}

    for results in results_per_query:
        for rank, chunk in enumerate(results):
            cid = chunk.content_hash
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in chunks:
                chunks[cid] = chunk

    sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    selected = sorted_ids[:max_chunks]
    return [chunks[cid] for cid in selected], {cid: scores[cid] for cid in selected}


async def execute_single_query(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    top_k: int,
    recency_weight: float,
    scope: str | None,
    source_prefixes: list[str] | None,
) -> list[RetrievedChunk]:
    """Execute one RAG search and parse results into chunks."""
    body: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "recency_weight": recency_weight,
    }
    if source_prefixes:
        body["source_prefixes"] = source_prefixes
    elif scope:
        body["scope"] = scope

    response = await client.post(endpoint, json=body)
    response.raise_for_status()
    data = response.json()

    raw_chunks: list[str] = data.get("chunks", [])
    metadata: list[dict[str, Any]] = data.get("metadata", [])

    return [
        RetrievedChunk(
            content=chunk,
            source=str(meta.get("source", "unknown")),
            indexed_at=str(meta.get("indexed_at", "unknown")),
            metadata=meta,
        )
        for chunk, meta in zip(raw_chunks, metadata, strict=True)
    ]
