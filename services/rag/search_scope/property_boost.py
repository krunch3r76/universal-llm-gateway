"""Property-index distance boost for hybrid search."""

from __future__ import annotations

import re

from services.rag.property_index import PropertyIndex

__all__ = ["apply_property_boost", "extract_entity_terms"]

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
    """Apply distance boost to chunks that match property index entries."""
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
