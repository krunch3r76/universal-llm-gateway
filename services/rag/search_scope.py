"""Search scope resolution, property boost, BM25 sidecar, and recency sort.

Implements per-query enhancements within the RAG service's ``/search`` endpoint.
These operate _inside_ Pool A (the dense+sparse hybrid path) before results
are returned to the pipeline handler for cross-pool RRF merge, source
habituation, and Pool B swap.

  Property boost (hybrid search):
    Queries the SQLite property inverted index for entity names, types, facets,
    topics, and relations extracted at index time.  Chunks appearing in both the
    vector results and the property index receive a configurable distance discount
    (``property_boost_factor``), surfacing structurally relevant chunks that rank
    below top-k on cosine alone.  Applied by ``apply_property_boost()``.

  BM25 sidecar:
    Sparse BM25 candidates from the FTS5 index are merged with dense vector
    results via mini-RRF within each ``/search`` call. This is Pool A's internal
    keyword component — distinct from Pool B, which runs independently at the
    pipeline layer with vocabulary-aware expansion.

  Recency sort:
    Adds an additive bonus to chunks based on ``indexed_at`` timestamp using
    exponential decay (``RECENCY_DECAY_LAMBDA``).  Controlled per-request via
    ``recency_weight`` (0 = pure cosine, 1 = recency-dominant).
    Applied by ``apply_recency_sort()``.

  Scope resolution:
    Maps a named scope (e.g. ``"project"``, ``"research"``) to ``source_prefixes``
    defined in the RAG config.  Enables per-collection retrieval without exposing
    raw filesystem paths to callers.  Applied by ``resolve_scope_request()``.

These are pure functions; runtime state lives in ``services.rag.rag_service.state``.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

import chromadb
from fastapi import HTTPException

from services.rag.config import RagConfig
from services.rag.fts_index import FtsIndex
from services.rag.models import RECENCY_DECAY_LAMBDA, SearchRequest
from services.rag.property_index import PropertyIndex


def require_loaded_config(config: RagConfig | None) -> RagConfig:
    if config is None:
        raise HTTPException(status_code=503, detail="RAG config not loaded")
    return config


def resolve_scope_request(
    request: SearchRequest,
    config: RagConfig | None,
) -> SearchRequest:
    if request.scope and request.source_prefixes:
        raise HTTPException(
            status_code=400,
            detail="'scope' and 'source_prefixes' are mutually exclusive",
        )
    scope_names: list[str]
    if request.scope is None:
        return request
    scope_names = [request.scope] if isinstance(request.scope, str) else request.scope
    if not scope_names:
        raise HTTPException(
            status_code=400,
            detail="scope cannot be empty list",
        )

    loaded_config = require_loaded_config(config)
    merged_prefixes: list[str] = []
    seen: set[str] = set()
    for name in scope_names:
        scope_def = loaded_config.scopes.get(name)
        if scope_def is None:
            available = sorted(loaded_config.scopes)
            available_display = ", ".join(available)
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scope {name!r}. Available: {available_display}",
            )
        for p in scope_def.prefixes:
            if p not in seen:
                seen.add(p)
                merged_prefixes.append(p)
    return request.model_copy(update={"source_prefixes": merged_prefixes})


def apply_source_prefix_filter(
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    source_prefixes: list[str] | None,
    top_k: int,
) -> tuple[list[str], list[dict[str, str | int | float | bool]], list[float]]:
    if not source_prefixes:
        return chunks, metadatas, distances
    filtered = [
        (chunk, metadata, distance)
        for chunk, metadata, distance in zip(chunks, metadatas, distances, strict=True)
        if _matches_source_prefix(str(metadata.get("source", "")), source_prefixes)
    ]
    return (
        [item[0] for item in filtered][:top_k],
        [item[1] for item in filtered][:top_k],
        [item[2] for item in filtered][:top_k],
    )


def apply_source_prefix_filter_with_ids(
    ids: list[str],
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    source_prefixes: list[str] | None,
    top_k: int,
) -> tuple[
    list[str],
    list[str],
    list[dict[str, str | int | float | bool]],
    list[float],
]:
    """Source prefix filter that keeps chunk IDs in sync."""
    if not source_prefixes:
        return ids, chunks, metadatas, distances
    filtered = [
        (rid, chunk, metadata, distance)
        for rid, chunk, metadata, distance in zip(
            ids, chunks, metadatas, distances, strict=True
        )
        if _matches_source_prefix(str(metadata.get("source", "")), source_prefixes)
    ][:top_k]
    return (
        [t[0] for t in filtered],
        [t[1] for t in filtered],
        [t[2] for t in filtered],
        [t[3] for t in filtered],
    )


def apply_max_distance_filter(
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    max_distance: float | None,
) -> tuple[list[str], list[dict[str, str | int | float | bool]], list[float]]:
    if max_distance is None:
        return chunks, metadatas, distances
    filtered = [
        (chunk, metadata, distance)
        for chunk, metadata, distance in zip(chunks, metadatas, distances, strict=True)
        if distance <= max_distance
    ]
    if not filtered:
        return [], [], []
    return (
        [item[0] for item in filtered],
        [item[1] for item in filtered],
        [item[2] for item in filtered],
    )


def apply_recency_sort(
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    recency_weight: float,
) -> tuple[list[str], list[dict[str, str | int | float | bool]], list[float]]:
    if recency_weight <= 0.0 or not chunks:
        return chunks, metadatas, distances
    adjusted = _apply_recency(distances, metadatas, recency_weight)
    sorted_triples = sorted(
        zip(chunks, metadatas, distances, adjusted, strict=True),
        key=lambda item: item[3],
    )
    return (
        [item[0] for item in sorted_triples],
        [item[1] for item in sorted_triples],
        [item[2] for item in sorted_triples],
    )


def _matches_source_prefix(source: str, prefixes: list[str]) -> bool:
    return any(source.startswith(prefix) for prefix in prefixes)


def _apply_recency(
    distances: list[float],
    metadatas: list[dict[str, str | int | float | bool]],
    recency_weight: float,
) -> list[float]:
    now = datetime.now(UTC)
    result: list[float] = []
    for distance, metadata in zip(distances, metadatas, strict=True):
        date_str = metadata.get("published_date") or metadata.get("indexed_at")
        if not isinstance(date_str, str):
            result.append(distance)
            continue
        try:
            doc_date = datetime.fromisoformat(date_str)
        except ValueError:
            result.append(distance)
            continue
        if doc_date.tzinfo is None:
            doc_date = doc_date.replace(tzinfo=UTC)
        days_old = max((now - doc_date).total_seconds() / 86400, 0.0)
        recency_score = math.exp(-RECENCY_DECAY_LAMBDA * days_old)
        adjusted_distance = (
            distance * (1 - recency_weight) - recency_weight * recency_score
        )
        result.append(adjusted_distance)
    return result


# ---------------------------------------------------------------------------
# Hybrid search: property index boost
# ---------------------------------------------------------------------------

_CAPITALIZED_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]+\b")
_PORT_RE = re.compile(r"\b\d{4,5}\b")
_STOP_WORDS = frozenset(
    {
        "The",
        "This",
        "That",
        "What",
        "Where",
        "When",
        "Which",
        "How",
        "Does",
        "Should",
        "Could",
        "Would",
        "About",
        "From",
        "With",
        "Into",
        "Each",
        "Every",
        "Also",
        "Many",
        "Some",
        "Other",
    }
)


def extract_entity_terms(query: str) -> list[str]:
    """Extract likely entity terms from a query via lightweight regex."""
    capitalized = _CAPITALIZED_RE.findall(query)
    ports = _PORT_RE.findall(query)
    terms = [t for t in capitalized if t not in _STOP_WORDS] + ports
    return list(dict.fromkeys(terms))


def apply_property_boost(
    ids: list[str],
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    query: str,
    property_index: PropertyIndex,
    boost_factor: float,
) -> tuple[
    list[str],
    list[str],
    list[dict[str, str | int | float | bool]],
    list[float],
    int,
]:
    """Apply distance boost to chunks that match property index entries.

    Returns (ids, chunks, metadatas, distances, property_hit_count).
    If no property hits, returns inputs unchanged with hit_count=0.
    """
    terms = extract_entity_terms(query)
    if not terms:
        return ids, chunks, metadatas, distances, 0

    hit_chunk_ids: set[str] = set()
    for term in terms:
        hit_chunk_ids.update(property_index.lookup(f"prop.name@@{term}"))

    if not hit_chunk_ids:
        return ids, chunks, metadatas, distances, 0

    boosted_distances: list[float] = []
    hit_count = 0
    for chunk_id, dist in zip(ids, distances, strict=True):
        if chunk_id in hit_chunk_ids:
            boosted_distances.append(dist * boost_factor)
            hit_count += 1
        else:
            boosted_distances.append(dist)

    return ids, chunks, metadatas, boosted_distances, hit_count


# ---------------------------------------------------------------------------
# BM25 sparse sidecar — rank-based fusion with dense results
# ---------------------------------------------------------------------------

_BM25_RRF_K = 20


def apply_bm25_sidecar(
    ids: list[str],
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    query: str,
    fts: FtsIndex,
    collection: chromadb.Collection,
    source_prefixes: list[str] | None,
    *,
    bm25_limit: int = 30,
    rrf_k: int = _BM25_RRF_K,
) -> tuple[
    list[str],
    list[str],
    list[dict[str, str | int | float | bool]],
    list[float],
    int,
]:
    """Merge BM25 results into the dense candidate set via mini-RRF.

    Returns (ids, chunks, metadatas, distances, bm25_hit_count).
    BM25-only chunks (not in dense set) are fetched from ChromaDB for their
    document text and metadata, then inserted with a synthetic distance
    derived from the RRF score relative to the dense tail.
    """
    try:
        if source_prefixes:
            bm25_hits = fts.search_scoped(query, source_prefixes, limit=bm25_limit)
        else:
            bm25_hits = fts.search(query, limit=bm25_limit)
    except Exception:
        return ids, chunks, metadatas, distances, 0

    if not bm25_hits:
        return ids, chunks, metadatas, distances, 0

    dense_set = set(ids)

    # RRF scores for dense results (rank-only, score is 1/(k+rank+1))
    rrf_scores: dict[str, float] = {}
    for rank, cid in enumerate(ids):
        rrf_scores[cid] = 1.0 / (rrf_k + rank + 1)

    # Add BM25 RRF contribution
    bm25_only_ids: list[str] = []
    bm25_hit_count = 0
    for bm25_rank, (cid, _score) in enumerate(bm25_hits):
        bm25_rrf = 1.0 / (rrf_k + bm25_rank + 1)
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + bm25_rrf
        if cid in dense_set:
            bm25_hit_count += 1
        else:
            bm25_only_ids.append(cid)

    if not bm25_only_ids:
        # All BM25 hits already in dense set — just re-sort by RRF
        combined = sorted(
            zip(ids, chunks, metadatas, distances, strict=True),
            key=lambda t: rrf_scores.get(t[0], 0.0),
            reverse=True,
        )
        return (
            [t[0] for t in combined],
            [t[1] for t in combined],
            [t[2] for t in combined],
            [t[3] for t in combined],
            bm25_hit_count,
        )

    # Fetch BM25-only chunks from ChromaDB
    try:
        fetched = collection.get(ids=bm25_only_ids, include=["documents", "metadatas"])
    except Exception:
        return ids, chunks, metadatas, distances, bm25_hit_count

    fetched_ids: list[str] = fetched.get("ids") or []
    fetched_docs_raw = fetched.get("documents")
    fetched_metas_raw = fetched.get("metadatas")
    fetched_docs = (
        fetched_docs_raw if isinstance(fetched_docs_raw, list) else [""] * len(fetched_ids)
    )
    if len(fetched_docs) < len(fetched_ids):
        fetched_docs = fetched_docs + ([""] * (len(fetched_ids) - len(fetched_docs)))
    else:
        fetched_docs = fetched_docs[: len(fetched_ids)]
    fetched_metas_list = (
        fetched_metas_raw if isinstance(fetched_metas_raw, list) else [{}] * len(fetched_ids)
    )
    if len(fetched_metas_list) < len(fetched_ids):
        fetched_metas_list = fetched_metas_list + ([{}] * (len(fetched_ids) - len(fetched_metas_list)))
    else:
        fetched_metas_list = fetched_metas_list[: len(fetched_ids)]

    # Synthetic distance: slightly worse than the worst dense result
    tail_distance = max(distances) * 1.1 if distances else 1.0

    all_ids = list(ids)
    all_chunks = list(chunks)
    all_metadatas = list(metadatas)
    all_distances = list(distances)

    fetched_map = {}
    for fid, doc, meta in zip(fetched_ids, fetched_docs, fetched_metas_list):
        if not isinstance(meta, dict):
            continue
        fetched_map[fid] = (doc if isinstance(doc, str) else "", meta)
    for cid in bm25_only_ids:
        if cid in fetched_map:
            doc, meta = fetched_map[cid]
            all_ids.append(cid)
            all_chunks.append(doc or "")
            all_metadatas.append(meta)
            all_distances.append(tail_distance)
            bm25_hit_count += 1

    if source_prefixes:
        all_ids, all_chunks, all_metadatas, all_distances = (
            apply_source_prefix_filter_with_ids(
                ids=all_ids,
                chunks=all_chunks,
                metadatas=all_metadatas,
                distances=all_distances,
                source_prefixes=source_prefixes,
                top_k=len(all_ids),
            )
        )

    # Re-sort everything by RRF score
    combined = sorted(
        zip(all_ids, all_chunks, all_metadatas, all_distances, strict=True),
        key=lambda t: rrf_scores.get(t[0], 0.0),
        reverse=True,
    )
    return (
        [t[0] for t in combined],
        [t[1] for t in combined],
        [t[2] for t in combined],
        [t[3] for t in combined],
        bm25_hit_count,
    )
