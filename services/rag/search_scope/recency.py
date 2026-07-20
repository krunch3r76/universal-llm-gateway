"""Recency-weighted distance adjustment for search ranking."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from services.rag.models import RECENCY_DECAY_LAMBDA

__all__ = ["apply_recency_sort"]


def apply_recency_sort(
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    recency_weight: float,
) -> tuple[list[str], list[dict[str, str | int | float | bool]], list[float]]:
    """Reorder results by recency-adjusted score while preserving raw distances."""
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
