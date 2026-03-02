from __future__ import annotations

import math
from datetime import UTC, datetime

from fastapi import HTTPException

from services.rag.config import RagConfig
from services.rag.models import DECAY_LAMBDA, SearchRequest


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
    if not request.scope:
        return request

    loaded_config = require_loaded_config(config)
    scope_def = loaded_config.scopes.get(request.scope)
    if scope_def is None:
        available = sorted(loaded_config.scopes)
        available_display = ", ".join(available)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scope '{request.scope}'. Available: {available_display}",
        )
    return request.model_copy(update={"source_prefixes": scope_def.prefixes})


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
        recency_score = math.exp(-DECAY_LAMBDA * days_old)
        adjusted_distance = (
            distance * (1 - recency_weight) - recency_weight * recency_score
        )
        result.append(adjusted_distance)
    return result
