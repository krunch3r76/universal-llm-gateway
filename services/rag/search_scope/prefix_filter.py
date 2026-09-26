"""Source-prefix and max-distance filters for parallel search result lists.

Helpers operate on the parallel ``chunks``/``metadatas``/``distances`` lists
(optionally with chunk ``ids``) produced by Chroma queries and keep them aligned.
``execute_search`` in ``services.rag.rag_service.search`` applies the prefix
filter to scope results and the max-distance ceiling as a raw-cosine relevance
gate; ``bm25_sidecar`` reuses the ID-aware prefix filter on BM25-only rows.
"""

from __future__ import annotations

__all__ = [
    "apply_max_distance_filter",
    "apply_source_prefix_filter",
    "apply_source_prefix_filter_with_ids",
    "matches_source_prefix",
]


def matches_source_prefix(source: str, prefixes: list[str]) -> bool:
    """Return True when source path starts with any configured prefix."""
    return any(source.startswith(prefix) for prefix in prefixes)


def apply_source_prefix_filter(
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    source_prefixes: list[str] | None,
    top_k: int,
) -> tuple[list[str], list[dict[str, str | int | float | bool]], list[float]]:
    """Filter search rows to those whose source matches any prefix."""
    if not source_prefixes:
        return chunks, metadatas, distances
    filtered = [
        (chunk, metadata, distance)
        for chunk, metadata, distance in zip(chunks, metadatas, distances, strict=True)
        if matches_source_prefix(str(metadata.get("source", "")), source_prefixes)
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
    """Keep rows whose metadata ``source`` starts with a prefix, IDs kept in sync.

    ID-aware variant of ``apply_source_prefix_filter`` used by ``execute_search``
    and by ``apply_bm25_sidecar``. Returns inputs unchanged when
    ``source_prefixes`` is empty or None; otherwise returns the matching rows,
    in original order, truncated to ``top_k``.
    """
    if not source_prefixes:
        return ids, chunks, metadatas, distances
    filtered = [
        (rid, chunk, metadata, distance)
        for rid, chunk, metadata, distance in zip(
            ids, chunks, metadatas, distances, strict=True
        )
        if matches_source_prefix(str(metadata.get("source", "")), source_prefixes)
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
    """Drop rows whose distance exceeds the configured ``max_distance`` ceiling.

    Rows with ``distance <= max_distance`` are kept in original order. A None
    ceiling is a no-op. Does not handle chunk IDs, so ``execute_search`` filters
    its ID list in parallel before calling this. Returns three empty lists when
    nothing survives.
    """
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
