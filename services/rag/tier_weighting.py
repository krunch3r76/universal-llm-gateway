"""Provenance-tier distance weighting for RAG retrieval.

Tier weighting multiplies the cosine distance of retrieved chunks by a
per-tier factor, boosting (weight < 1.0) or demoting (weight > 1.0) chunks
based on their ``provenance_tier`` metadata value.

Canonical tier values and metadata key:
    metadata_overrides={"provenance_tier": "court_record"}   # Tier 1 — Core Authority
    metadata_overrides={"provenance_tier": "regulator_pub"}  # Tier 1 — Core Authority
    metadata_overrides={"provenance_tier": "practitioner_analysis"}  # Tier 2
    metadata_overrides={"provenance_tier": "expert_commentary"}      # Tier 3

Apply at ingest time; the ``tier_weight`` parameter on /search selects which
tiers to boost and by how much.
"""

from __future__ import annotations


def apply_tier_weight(
    ids: list[str],
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    tier_weight: dict[str, float],
) -> tuple[
    list[str],
    list[str],
    list[dict[str, str | int | float | bool]],
    list[float],
    int,
]:
    """Apply provenance-tier distance multipliers to retrieved chunks.

    Chunks carrying a ``provenance_tier`` metadata key (e.g. ``court_record``,
    ``regulator_pub``) are boosted or penalised by multiplying their cosine
    distance by the corresponding factor in ``tier_weight``.  Values < 1.0
    reduce distance (boost rank); values > 1.0 increase distance (demote).
    Chunks without a matching ``provenance_tier`` value are unaffected.

    Must be called after ``apply_max_distance_filter`` so that the distance
    gate operates on raw cosine distances, not tier-adjusted ones.

    Results are re-sorted by adjusted distance after weighting so that
    boosted chunks surface in the correct position even when the caller
    does not apply a subsequent ``recency_weight`` sort.

    Returns (ids, chunks, metadatas, adjusted_distances, tier_hit_count).
    If ``tier_weight`` is empty or no chunk has a matching tag, the inputs
    are returned unchanged with hit_count=0.
    """
    if not tier_weight or not chunks:
        return ids, chunks, metadatas, distances, 0

    adjusted: list[float] = []
    hit_count = 0
    for meta, dist in zip(metadatas, distances, strict=True):
        tier = meta.get("provenance_tier")
        if isinstance(tier, str) and tier in tier_weight:
            adjusted.append(dist * tier_weight[tier])
            hit_count += 1
        else:
            adjusted.append(dist)

    if hit_count == 0:
        return ids, chunks, metadatas, distances, 0

    sorted_quads = sorted(
        zip(ids, chunks, metadatas, adjusted, strict=True),
        key=lambda t: t[3],
    )
    return (
        [t[0] for t in sorted_quads],
        [t[1] for t in sorted_quads],
        [t[2] for t in sorted_quads],
        [t[3] for t in sorted_quads],
        hit_count,
    )
