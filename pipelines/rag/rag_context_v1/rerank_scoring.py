"""Scoring, windowing, and bounded movement for LLM reranking.

Pure functions with no pipeline dependencies — used by
``rag_rerank_assemble_v1`` handler.
"""

from __future__ import annotations

from typing import Any

from services.rag.entity_merging import (
    extract_entities_from_metadata,
    extract_topics_from_metadata,
)

from .context_formatting import ChunkData

_RANK_SCORES = [1.0, 0.75, 0.5, 0.25, 0.1]

_EXCERPT_TOKEN_LIMIT = 150


def truncate_excerpt(text: str, limit: int = _EXCERPT_TOKEN_LIMIT) -> str:
    """Truncate text to approximately ``limit`` tokens (word-based estimate)."""
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + " …"


def compact_metadata_summary(metadata: dict[str, Any]) -> tuple[str, str]:
    """Build compact entity and topic strings from chunk metadata."""
    entities = extract_entities_from_metadata(metadata)
    topics = extract_topics_from_metadata(metadata)

    entity_names = [f"{e.name}({','.join(e.type)})" for e in entities[:5]]
    topic_labels = list(topics[:3])

    return ", ".join(entity_names) or "none", ", ".join(topic_labels) or "none"


def build_candidate_block(chunk: ChunkData, prior_rank: int) -> str:
    """Format one chunk as a candidate block for the reranking prompt."""
    entities_str, topics_str = compact_metadata_summary(chunk["metadata"])
    excerpt = truncate_excerpt(chunk["content"])
    cid = chunk["content_hash"][:8]
    return (
        f"[Chunk {cid}]\n"
        f"Prior rank: {prior_rank + 1}\n"
        f"Prior score: {chunk['score']:.3f}\n"
        f"Excerpt: {excerpt}\n"
        f"Entities: [{entities_str}]\n"
        f"Topics: [{topics_str}]"
    )


def build_windows(count: int, window_size: int, overlap: int) -> list[list[int]]:
    """Build overlapping window index lists over ``count`` items."""
    if count <= window_size:
        return [list(range(count))]

    windows: list[list[int]] = []
    step = max(1, window_size - overlap)
    start = 0
    while start < count:
        end = min(start + window_size, count)
        windows.append(list(range(start, end)))
        if end >= count:
            break
        start += step
    return windows


def aggregate_window_scores(
    window_rankings: list[dict[str, list[dict[str, Any]]]],
    windows: list[list[int]],
    chunk_ids: list[str],
) -> tuple[dict[str, float], dict[str, str]]:
    """Aggregate per-window LLM rankings into a single score per chunk.

    Returns (llm_scores, confidence_map).
    """
    score_sums: dict[str, float] = {cid: 0.0 for cid in chunk_ids}
    appearance_count: dict[str, int] = {cid: 0 for cid in chunk_ids}
    confidence_map: dict[str, str] = {}

    for w_idx, ranking_data in enumerate(window_rankings):
        ranking_list = ranking_data.get("ranking", [])
        id_to_rank: dict[str, int] = {}
        for entry in ranking_list:
            eid = str(entry.get("chunk_id", ""))
            rank = int(entry.get("rank", 99)) - 1
            id_to_rank[eid] = rank
            conf = str(entry.get("confidence", "medium"))
            if eid not in confidence_map or conf == "high":
                confidence_map[eid] = conf

        window_chunk_ids = [chunk_ids[i] for i in windows[w_idx] if i < len(chunk_ids)]
        for cid in window_chunk_ids:
            rank = id_to_rank.get(cid, len(window_chunk_ids))
            pos_score = _RANK_SCORES[rank] if rank < len(_RANK_SCORES) else 0.1
            score_sums[cid] += pos_score
            appearance_count[cid] += 1

    llm_scores: dict[str, float] = {}
    for cid in chunk_ids:
        if appearance_count[cid] > 0:
            llm_scores[cid] = score_sums[cid] / appearance_count[cid]
        else:
            llm_scores[cid] = 0.0

    return llm_scores, confidence_map


def apply_bounded_movement(
    chunks: list[ChunkData],
    final_scores: dict[str, float],
    max_movement: int,
    confidence_map: dict[str, str] | None = None,
) -> list[ChunkData]:
    """Sort by final_scores with bounded rank movement from prior order.

    Each chunk can move at most ``max_movement`` positions from its prior
    rank.  Movements > 2 require LLM confidence = "high".
    """
    n = len(chunks)
    prior_rank = {c["content_hash"][:8]: i for i, c in enumerate(chunks)}
    scored = sorted(
        chunks,
        key=lambda c: final_scores.get(c["content_hash"][:8], 0.0),
        reverse=True,
    )

    if confidence_map is None:
        confidence_map = {}

    result: list[ChunkData | None] = [None] * n
    placed: set[int] = set()
    placed_chunks: set[str] = set()

    for c in scored:
        cid = c["content_hash"][:8]
        old_pos = prior_rank.get(cid, n - 1)
        new_pos = scored.index(c)

        movement = abs(new_pos - old_pos)
        conf = confidence_map.get(cid, "medium")
        effective_max = max_movement if conf == "high" else min(max_movement, 2)

        if movement > effective_max:
            direction = 1 if new_pos > old_pos else -1
            new_pos = old_pos + direction * effective_max
            new_pos = max(0, min(n - 1, new_pos))

        while new_pos in placed and new_pos < n - 1:
            new_pos += 1
        while new_pos in placed and new_pos > 0:
            new_pos -= 1

        if new_pos not in placed:
            result[new_pos] = c
            placed.add(new_pos)
            placed_chunks.add(cid)

    remaining = [c for c in chunks if c["content_hash"][:8] not in placed_chunks]
    for i in range(n):
        if result[i] is None and remaining:
            result[i] = remaining.pop(0)

    return [c for c in result if c is not None]
