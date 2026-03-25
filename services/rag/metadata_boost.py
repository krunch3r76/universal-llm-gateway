"""Post-RRF metadata boost for RAG retrieval.

Re-scores RRF-merged chunks using overlap between query-derived terms and
extracted entity/relation/topic metadata. Deterministic — no LLM calls. The
metadata used here was extracted at index time by the knowledge extraction
pipeline and stored in the property index; this module leverages that
index-time investment at search time without re-analysis.

Called from the ``rag_multi_retrieve_v1`` pipeline handler between RRF merge
and source habituation. Complements the per-query property_boost in
``search_scope.py`` which operates pre-RRF on individual query results.

Scoring:
    meta_score(chunk) = Σ entity_name_overlap * 2.0
                      + Σ relation_overlap * 1.5
                      + Σ topic_overlap * 1.0

    final(chunk) = (1 - w) * rrf_norm(chunk) + w * meta_norm(chunk)
    where rrf_norm and meta_norm are each divided by their respective max.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.rag.entity_merging import (
    extract_entities_from_metadata,
    extract_topics_from_metadata,
)

_CAPITALIZED_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]+\b")
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

_ENTITY_WEIGHT = 2.0
_RELATION_WEIGHT = 1.5
_TOPIC_WEIGHT = 1.0


@dataclass(slots=True, kw_only=True)
class MetadataBoostResult:
    """Result of metadata boost on RRF-merged chunks."""

    chunks: list[Any]
    scores: dict[str, float]
    metadata_hit_count: int
    avg_metadata_score: float
    applied: bool


def extract_query_terms(
    original_query: str,
    rewritten_queries: list[str],
) -> set[str]:
    """Build term set from original query entities + rewritten query tokens.

    Original query: extract capitalized entity-like terms (same heuristic as
    search_scope.py).  Rewritten queries: include ALL tokens since the LLM
    already distilled them to keyword phrases.
    """
    terms: set[str] = set()

    for word in _CAPITALIZED_RE.findall(original_query):
        if word not in _STOP_WORDS:
            terms.add(word.lower())

    for query in rewritten_queries:
        for token in query.lower().split():
            stripped = token.strip(".,;:!?\"'()[]")
            if len(stripped) >= 2:
                terms.add(stripped)

    return terms


def _compute_chunk_score(
    metadata: dict[str, object],
    query_terms: set[str],
) -> float:
    """Compute raw metadata overlap score for one chunk."""
    entities = extract_entities_from_metadata(metadata)
    topics = extract_topics_from_metadata(metadata)

    score = 0.0

    for entity in entities:
        if entity.name.lower() in query_terms:
            score += _ENTITY_WEIGHT
        for rel in entity.relations:
            if rel.target.lower() in query_terms or entity.name.lower() in query_terms:
                score += _RELATION_WEIGHT

    for topic in topics:
        if topic.lower() in query_terms:
            score += _TOPIC_WEIGHT

    return score


def _apply_coverage_selection(
    chunks: list[Any],
    scores: dict[str, float],
    max_chunks: int,
) -> tuple[list[Any], dict[str, float]]:
    """Greedy selection maximising unique entity coverage.

    First half of max_chunks selected purely by score (preserves top-ranked
    quality).  Remaining slots prefer chunks that introduce new entities.
    """
    if len(chunks) <= max_chunks:
        return chunks, scores

    ranked = sorted(chunks, key=lambda c: scores[c.content_hash], reverse=True)

    score_floor = max_chunks // 2
    selected = ranked[:score_floor]
    covered: set[str] = set()
    for c in selected:
        for e in extract_entities_from_metadata(c.metadata):
            covered.add(e.name.lower())

    remaining = ranked[score_floor:]
    for chunk in remaining:
        if len(selected) >= max_chunks:
            break
        chunk_entities = {
            e.name.lower() for e in extract_entities_from_metadata(chunk.metadata)
        }
        new_coverage = chunk_entities - covered
        if new_coverage:
            selected.append(chunk)
            covered.update(new_coverage)

    if len(selected) < max_chunks:
        used_hashes = {c.content_hash for c in selected}
        for chunk in remaining:
            if len(selected) >= max_chunks:
                break
            if chunk.content_hash not in used_hashes:
                selected.append(chunk)

    selected_scores = {c.content_hash: scores[c.content_hash] for c in selected}
    return selected, selected_scores


def apply_metadata_boost(
    chunks: list[Any],
    rrf_scores: dict[str, float],
    original_query: str,
    rewritten_queries: list[str],
    *,
    enabled: bool = True,
    weight: float = 0.20,
    coverage_enabled: bool = False,
    max_chunks: int = 20,
) -> MetadataBoostResult:
    """Apply metadata boost to RRF-merged chunks.

    Args:
        chunks: RRF-merged chunks (have .content_hash and .metadata attrs).
        rrf_scores: Map of content_hash → RRF score.
        original_query: User's original query text.
        rewritten_queries: LLM-generated keyword queries.
        enabled: Master toggle (pipeline option metadata_boost_enabled).
        weight: Metadata weight in fusion (pipeline option metadata_boost_weight).
        coverage_enabled: Greedy entity coverage selection (pipeline option).
        max_chunks: Maximum chunks to return.

    Returns:
        MetadataBoostResult with re-ranked chunks and fused scores.
    """
    if not enabled or not chunks:
        return MetadataBoostResult(
            chunks=chunks,
            scores=rrf_scores,
            metadata_hit_count=0,
            avg_metadata_score=0.0,
            applied=False,
        )

    query_terms = extract_query_terms(original_query, rewritten_queries)
    if not query_terms:
        return MetadataBoostResult(
            chunks=chunks,
            scores=rrf_scores,
            metadata_hit_count=0,
            avg_metadata_score=0.0,
            applied=False,
        )

    raw_meta: dict[str, float] = {}
    for chunk in chunks:
        raw_meta[chunk.content_hash] = _compute_chunk_score(chunk.metadata, query_terms)

    max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0
    max_meta = max(raw_meta.values()) if raw_meta else 1.0

    if max_rrf == 0.0:
        max_rrf = 1.0
    if max_meta == 0.0:
        max_meta = 1.0

    fused: dict[str, float] = {}
    for chunk in chunks:
        cid = chunk.content_hash
        rrf_n = rrf_scores.get(cid, 0.0) / max_rrf
        meta_n = raw_meta.get(cid, 0.0) / max_meta
        fused[cid] = (1.0 - weight) * rrf_n + weight * meta_n

    hit_count = sum(1 for v in raw_meta.values() if v > 0.0)
    avg_score = sum(raw_meta.values()) / len(chunks)

    if coverage_enabled:
        result_chunks, result_scores = _apply_coverage_selection(
            chunks, fused, max_chunks
        )
    else:
        result_chunks = sorted(
            chunks, key=lambda c: fused[c.content_hash], reverse=True
        )[:max_chunks]
        result_scores = {c.content_hash: fused[c.content_hash] for c in result_chunks}

    return MetadataBoostResult(
        chunks=result_chunks,
        scores=result_scores,
        metadata_hit_count=hit_count,
        avg_metadata_score=avg_score,
        applied=True,
    )
