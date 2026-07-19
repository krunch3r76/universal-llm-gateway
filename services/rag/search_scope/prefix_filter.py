"""Source-prefix and max-distance filters for search result lists."""

from __future__ import annotations

__all__ = [
    "apply_max_distance_filter",
    "apply_source_prefix_filter",
    "apply_source_prefix_filter_with_ids",
    "matches_source_prefix",
]


def matches_source_prefix(source: str, prefixes: list[str]) -> bool:
    return any(source.startswith(prefix) for prefix in prefixes)


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
    """Source prefix filter that keeps chunk IDs in sync."""
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
