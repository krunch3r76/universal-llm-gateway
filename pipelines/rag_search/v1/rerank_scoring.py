"""Scoring, windowing, and bounded movement for reranking.

Pure functions with no pipeline dependencies — used by the
``rag_rerank_assemble_v1`` handler for both cross-encoder and generative paths.

Key function: ``apply_bounded_movement``
    Places chunks left to right inside a ±``max_movement`` window of their
    prior index (default 3; confidence other than "high" caps the window at
    2).  Each slot takes the highest final score that can sit there without
    stranding another chunk outside its window.

``aggregate_window_scores`` and ``build_windows`` are used only by the
    generative (sliding-window LLM) path.  The cross-encoder path skips windowing
    and goes directly to ``apply_bounded_movement``.
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


def _window_feasible(
    remaining: set[int],
    start: int,
    windows: list[tuple[int, int]],
    n: int,
) -> bool:
    """True when earliest-deadline placement can fill every later slot."""
    pool = set(remaining)
    for pos in range(start, n):
        eligible = [i for i in pool if windows[i][0] <= pos <= windows[i][1]]
        if not eligible:
            return False
        pick = min(eligible, key=lambda i: (windows[i][1], i))
        pool.remove(pick)
    return True


def apply_bounded_movement(
    chunks: list[ChunkData],
    final_scores: dict[str, float],
    max_movement: int,
    confidence_map: dict[str, str] | None = None,
) -> list[ChunkData]:
    """Reorder by final score without leaving a chunk's movement window.

    Each chunk may move at most ``max_movement`` positions from its prior
    index. Confidence other than ``high`` caps that at 2. Within those
    windows the order is the best available: each slot takes the highest
    final score that is allowed there and still leaves a feasible assignment
    for the rest. A collision used to walk outside the window and could put
    the lowest score in rank 1; this placement never does that.
    """
    n = len(chunks)
    if n <= 1:
        return list(chunks)
    if confidence_map is None:
        confidence_map = {}

    windows: list[tuple[int, int]] = []
    for i, chunk in enumerate(chunks):
        cid = chunk["content_hash"][:8]
        conf = confidence_map.get(cid, "medium")
        limit = max_movement if conf == "high" else min(max_movement, 2)
        windows.append((max(0, i - limit), min(n - 1, i + limit)))

    def _score(index: int) -> float:
        cid = chunks[index]["content_hash"][:8]
        return final_scores.get(cid, 0.0)

    remaining = set(range(n))
    order: list[int] = []
    for pos in range(n):
        eligible = [i for i in remaining if windows[i][0] <= pos <= windows[i][1]]
        eligible.sort(key=lambda i: (-_score(i), i))
        chosen: int | None = None
        for cand in eligible:
            if _window_feasible(remaining - {cand}, pos + 1, windows, n):
                chosen = cand
                break
        if chosen is None:
            raise RuntimeError(
                "bounded movement has no chunk allowed at this slot; "
                "windows must contain each prior index"
            )
        order.append(chosen)
        remaining.remove(chosen)
    return [chunks[i] for i in order]


WEAK_MATCH_THRESHOLD_DEFAULT = 0.3


def relevance_summary(
    ordered_chunks: list[ChunkData],
    *,
    final_scores: dict[str, float] | None = None,
    ce_scores: dict[str, float] | None = None,
    weak_match_threshold: float = WEAK_MATCH_THRESHOLD_DEFAULT,
) -> dict[str, Any]:
    """Per-chunk score rows in output order plus the weak-match verdict.

    This is what lets an author gate on relevance instead of reading every
    chunk: ``chunks[]`` rows carry ``rank``, ``chunk_id`` (``content_hash[:8]``),
    ``source``, the retrieval ``prior_score`` and — when a cross-encoder ran —
    ``ce_score`` (sigmoid relevance probability, 0–1) and the fused
    ``final_score``. ``top_relevance`` is the best ``ce_score``; ``weak_match``
    is ``top_relevance < weak_match_threshold`` and ``None`` when no
    cross-encoder scored the set (prior scores alone are rank-relative, not a
    relevance distance). Keys are flattened into the handler's ``StepOutput.json``
    and surfaced to MCP callers under ``retrieval``.
    """
    rows: list[dict[str, Any]] = []
    for rank, chunk in enumerate(ordered_chunks, start=1):
        cid = chunk["content_hash"][:8]
        row: dict[str, Any] = {
            "rank": rank,
            "chunk_id": cid,
            "source": chunk.get("source"),
            "prior_score": round(float(chunk.get("score", 0.0)), 4),
        }
        if ce_scores is not None and cid in ce_scores:
            row["ce_score"] = round(ce_scores[cid], 4)
        if final_scores is not None and cid in final_scores:
            row["final_score"] = round(final_scores[cid], 4)
        rows.append(row)

    summary: dict[str, Any] = {"chunks": rows}
    if ce_scores:
        top = max(ce_scores.values())
        summary["top_relevance"] = round(top, 4)
        summary["weak_match"] = top < weak_match_threshold
        summary["weak_match_threshold"] = weak_match_threshold
    else:
        summary["weak_match"] = None
    return summary


def resolve_rerank_mode(options: dict[str, Any] | None) -> str:
    """Return the rerank mode, defaulting to ``cross_encoder`` when unset.

    The direct ``rag-context`` pipeline relies on this default. The rewrite
    pipeline sets ``rerank_mode: generative`` explicitly so its LLM windows
    are not silently switched when the default changed to match the docstring.
    """
    effective = options or {}
    return str(effective.get("rerank_mode", "cross_encoder"))


def rerank_skip_status(enabled: bool, n_chunks: int, mode: str) -> str | None:
    """Return why this set should not be scored, or None when scoring should run.

    ``disabled`` covers a turned-off reranker and an empty candidate list.
    ``skipped_small_set`` covers three or fewer chunks in any mode other than
    ``cross_encoder``: reordering cannot matter and a generative window is not
    worth the call. Cross-encoder mode returns None for those small sets so
    the caller still scores them and can emit a real ``weak_match``.
    """
    if not enabled or n_chunks <= 0:
        return "disabled"
    if n_chunks <= 3 and mode != "cross_encoder":
        return "skipped_small_set"
    return None


def generative_rerank_status(n_windows: int, n_failures: int) -> str:
    """Status string for a generative rerank after its windows have run.

    Every window failing is ``error`` because the fused order is then
    prior-only. A positive failure count below that is
    ``partial_window_failures:<n>``. Zero failures is ``ok``.
    """
    if n_windows > 0 and n_failures >= n_windows:
        return "error"
    if n_failures > 0:
        return f"partial_window_failures:{n_failures}"
    return "ok"


def annotate_relevance(
    summary: dict[str, Any],
    *,
    rerank_status: str,
    weak_match_basis: str,
    rerank_error: str | None = None,
) -> dict[str, Any]:
    """Copy a relevance summary and name what ``weak_match`` is based on.

    ``weak_match`` keeps the meaning ``relevance_summary`` gave it.
    ``weak_match_basis`` is ``cross_encoder`` only when those scores exist;
    ``none`` means a null ``weak_match`` is not evidence the corpus is weak.
    ``rerank_error`` is set only on a named fallback or request failure.
    """
    annotated = dict(summary)
    annotated["rerank_status"] = rerank_status
    annotated["weak_match_basis"] = weak_match_basis
    if rerank_error:
        annotated["rerank_error"] = rerank_error
    return annotated
