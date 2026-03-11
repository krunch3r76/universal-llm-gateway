"""Search scope resolution, property boost, and recency sort for structured RAG.

This module implements the query-time enhancements applied to ChromaDB vector
search results before they are returned to the retrieval pipeline:

  Property boost (hybrid search):
    Queries the SQLite property inverted index for entity names, types, facets,
    topics, and relations extracted at index time.  Chunks appearing in both the
    vector results and the property index receive a configurable distance discount
    (``property_boost_factor``), surfacing structurally relevant chunks that rank
    below top-k on cosine alone.  Applied by ``apply_property_boost()``.

  Recency sort:
    Adds an additive bonus to chunks based on ``indexed_at`` timestamp using
    exponential decay (``RECENCY_DECAY_LAMBDA``).  Controlled per-request via
    ``recency_weight`` (0 = pure cosine, 1 = recency-dominant).
    Applied by ``apply_recency_sort()``.

  Scope resolution:
    Maps a named scope (e.g. ``"project"``, ``"research"``) to ``source_prefixes``
    defined in the RAG config.  Enables per-collection retrieval without exposing
    raw filesystem paths to callers.  Applied by ``resolve_scope_request()``.

These are pure functions; state lives in ``rag_service.py`` globals.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from fastapi import HTTPException

from services.rag.config import RagConfig
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
    if isinstance(request.scope, str):
        scope_names = [request.scope]
    else:
        if len(request.scope) == 0:
            raise HTTPException(
                status_code=400,
                detail="scope cannot be empty list",
            )
        scope_names = list(request.scope)

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
