"""RAG retrieval execution, facet computation, and RRF merging."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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
    k dampens the influence of lower ranks; higher k reduces rank sensitivity.

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
            chunks.setdefault(cid, chunk)

    sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    selected = sorted_ids[:max_chunks]
    return [chunks[cid] for cid in selected], {cid: scores[cid] for cid in selected}


async def execute_single_query(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    top_k: int,
    recency_weight: float,
    scope: str | list[str] | None,
    source_prefixes: list[str] | None,
    *,
    sparse_only: bool = False,
    query_embedding: list[float] | None = None,
) -> list[RetrievedChunk]:
    """Execute one RAG search and parse results into chunks.

    When ``query_embedding`` is provided, the RAG service skips its internal
    ``embed_query()`` call and uses the pre-computed vector directly. This
    enables batch embedding (one GPU forward pass for N queries).

    ∀ sparse_only=True: skip dense embedding; BM25/FTS5 only.
    """
    body: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "recency_weight": recency_weight,
    }
    if sparse_only:
        body["sparse_only"] = True
    if query_embedding is not None:
        body["query_embedding"] = query_embedding
    if source_prefixes:
        body["source_prefixes"] = source_prefixes
    elif scope is not None:
        body["scope"] = [scope] if isinstance(scope, str) else list(scope)

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


@dataclass(slots=True)
class NeighborExpansionResult:
    """Result of neighbor chunk expansion."""

    chunks: list[RetrievedChunk]
    scores: dict[str, float]
    neighbors_added: int
    neighbors_fetched: int
    sources_expanded: int


async def expand_neighbors(
    client: httpx.AsyncClient,
    endpoint: str,
    chunks: list[RetrievedChunk],
    scores: dict[str, float],
    *,
    n: int = 1,
    max_chunks: int = 30,
    score_discount: float = 1.0,
) -> NeighborExpansionResult:
    """Expand retrieved chunks with ±n neighbors from the same source.

    Fetches neighbors via POST /chunks_by_index (one HTTP round-trip).
    Deduplicates by content_hash, enforces max_chunks budget (originals
    always kept, neighbors fill remaining slots by score).
    """
    if n <= 0 or not chunks:
        return NeighborExpansionResult(
            chunks=chunks,
            scores=scores,
            neighbors_added=0,
            neighbors_fetched=0,
            sources_expanded=0,
        )

    existing_hashes: set[str] = {c.content_hash for c in chunks}
    source_indices: dict[str, set[int]] = {}
    chunk_scores_by_source: dict[str, dict[int, float]] = {}

    for chunk in chunks:
        source = chunk.source
        idx = chunk.metadata.get("chunk_index")
        if idx is None or int(idx) < 0:
            continue
        idx = int(idx)
        source_indices.setdefault(source, set()).add(idx)
        chunk_scores_by_source.setdefault(source, {})[idx] = scores.get(
            chunk.content_hash, 0.0
        )

    groups: list[dict[str, object]] = []
    neighbor_parent_scores: dict[tuple[str, int], float] = {}

    for source, indices in source_indices.items():
        needed: set[int] = set()
        for idx in indices:
            for delta in range(1, n + 1):
                for neighbor_idx in (idx - delta, idx + delta):
                    if neighbor_idx >= 0 and neighbor_idx not in indices:
                        needed.add(neighbor_idx)
                        parent_score = chunk_scores_by_source.get(source, {}).get(
                            idx, 0.0
                        )
                        key = (source, neighbor_idx)
                        neighbor_parent_scores[key] = max(
                            neighbor_parent_scores.get(key, 0.0), parent_score
                        )
        if needed:
            groups.append({"source": source, "chunk_indices": sorted(needed)})

    if not groups:
        return NeighborExpansionResult(
            chunks=chunks,
            scores=scores,
            neighbors_added=0,
            neighbors_fetched=0,
            sources_expanded=0,
        )

    try:
        response = await client.post(
            endpoint.replace("/search", "/chunks_by_index"),
            json={"groups": groups},
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(
            "expand_neighbors: /chunks_by_index call failed: %s", e, exc_info=True
        )
        raise

    raw_neighbors = data.get("chunks", [])
    neighbor_chunks: list[tuple[float, RetrievedChunk]] = []

    for item in raw_neighbors:
        chunk_index = item.get("chunk_index")
        if chunk_index is None or not isinstance(chunk_index, int):
            logger.warning("Missing or invalid chunk_index in neighbor item: %s", item)
            continue
        content = item.get("text")
        source = item.get("source")
        metadata = item.get("metadata")
        if not content or not source or not metadata:
            logger.warning(
                "Missing required fields (text, source, metadata) in neighbor item: %s",
                item,
            )
            continue
        rc = RetrievedChunk(
            content=content,
            source=source,
            indexed_at=str(metadata.get("indexed_at", "unknown")),
            metadata=metadata,
        )
        if rc.content_hash in existing_hashes:
            continue
        existing_hashes.add(rc.content_hash)
        parent_score = neighbor_parent_scores.get((item["source"], chunk_index), 0.0)
        discounted = parent_score * score_discount
        neighbor_chunks.append((discounted, rc))

    budget = max(0, max_chunks - len(chunks))
    neighbor_chunks.sort(key=lambda t: t[0], reverse=True)
    selected = neighbor_chunks[:budget]

    expanded_scores = dict(scores)
    expanded_chunks = list(chunks)
    for discounted_score, rc in selected:
        expanded_chunks.append(rc)
        expanded_scores[rc.content_hash] = discounted_score

    return NeighborExpansionResult(
        chunks=expanded_chunks,
        scores=expanded_scores,
        neighbors_added=len(selected),
        neighbors_fetched=len(raw_neighbors),
        sources_expanded=len(groups),
    )


def build_facet_pool(
    facets_raw: list[dict[str, object]] | object,
) -> list[tuple[str, str]]:
    """Convert facet dicts to (label, FTS5 OR-query) pairs for pool B retrieval."""
    if not isinstance(facets_raw, list):
        return []
    pool: list[tuple[str, str]] = []
    for facet in facets_raw:
        if not isinstance(facet, dict):
            continue
        label = str(facet.get("label", ""))
        terms = [t for t in facet.get("terms", []) if isinstance(t, str) and t.strip()]
        if not terms:
            continue
        fts_terms = [f'"{t}"' if " " in t else t for t in terms]
        pool.append((label, " OR ".join(fts_terms)))
    return pool


def compute_facets_from_text(
    source_text: str,
    *,
    max_phrases: int = 10,
    max_idf_terms: int = 8,
    max_discriminative: int = 4,
) -> list[dict[str, object]]:
    """Compute phrase facets + IDF expansion from query text (sync).

    Combines phrase extraction and IDF-weighted corpus expansion — the same
    logic as ExpandTermsHandler but callable as a plain function for concurrent
    execution alongside pool A retrieval queries.

    Intended to be run via ``asyncio.to_thread`` from the retrieve handler.
    """
    from .term_expansion import (
        extract_content_words,
        extract_phrases,
        idf_expand,
    )

    query_words = extract_content_words(source_text)
    query_word_set = frozenset(w.lower() for w in query_words)

    phrases = extract_phrases(source_text, max_phrases=max_phrases)

    emitted: set[str] = set(query_word_set)
    facets: list[dict[str, object]] = []
    for i, phrase in enumerate(phrases):
        terms: list[str] = [phrase]
        emitted.add(phrase.lower())
        for w in extract_content_words(phrase, min_len=3):
            wl = w.lower()
            terms.append(w)
            emitted.add(wl)
        facets.append({"label": f"query_facet_{i}", "terms": terms})

    idf_terms: list[str] = []
    if max_idf_terms > 0:
        raw_idf = idf_expand(
            query_words,
            max_discriminative=max_discriminative,
            max_results=max_idf_terms + len(emitted),
        )
        for t in raw_idf:
            if t.lower() not in emitted:
                emitted.add(t.lower())
                idf_terms.append(t)
            if len(idf_terms) >= max_idf_terms:
                break
        if idf_terms:
            facets.append({"label": "corpus_expansion", "terms": idf_terms})

    logger.info(
        "Inline facet computation: %d phrase facets + %d IDF terms. "
        "Phrases: %s. IDF: %s",
        len(phrases),
        len(idf_terms),
        phrases,
        idf_terms[:6],
    )
    return facets


async def compute_and_dispatch_pool_b(
    client: httpx.AsyncClient,
    endpoint: str,
    source_text: str,
    facet_top_k: int,
    recency_weight: float,
    scope: str | list[str] | None,
    source_prefixes: list[str] | None,
    *,
    max_phrases: int = 10,
    max_idf_terms: int = 8,
    max_discriminative: int = 4,
    multi_scope_labels: list[str] | None = None,
) -> tuple[
    list[dict[str, object]],
    list[tuple[str, str]],
    list[list[RetrievedChunk] | BaseException],
]:
    """Compute facets inline and dispatch pool B sparse queries.

    Runs IDF expansion via ``asyncio.to_thread`` (blocking SQL), then
    dispatches pool B sparse-only queries. Designed to run concurrently
    with pool A queries via ``asyncio.gather``.

    Returns:
        (computed_facets, facet_pool, pool_b_results)
    """
    facets = await asyncio.to_thread(
        compute_facets_from_text,
        source_text,
        max_phrases=max_phrases,
        max_idf_terms=max_idf_terms,
        max_discriminative=max_discriminative,
    )
    pool = build_facet_pool(facets)
    if not pool:
        return facets, pool, []

    tasks: list[asyncio.Task[list[RetrievedChunk]]] = []
    if multi_scope_labels:
        for _, or_query in pool:
            for scoped_label in multi_scope_labels:
                tasks.append(
                    asyncio.ensure_future(
                        execute_single_query(
                            client,
                            endpoint,
                            or_query,
                            facet_top_k,
                            recency_weight,
                            scoped_label,
                            None,
                            sparse_only=True,
                        )
                    )
                )
    else:
        for _, or_query in pool:
            tasks.append(
                asyncio.ensure_future(
                    execute_single_query(
                        client,
                        endpoint,
                        or_query,
                        facet_top_k,
                        recency_weight,
                        scope,
                        source_prefixes,
                        sparse_only=True,
                    )
                )
            )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    return facets, pool, list(results)
