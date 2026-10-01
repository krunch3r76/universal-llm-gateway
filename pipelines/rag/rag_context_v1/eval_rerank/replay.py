"""Offline replay of rerank order and fusion. No model calls.

``legacy_apply_bounded_movement`` is the cb67d209 collision walk, kept so the
before-metrics stay reproducible after the production function changes.
``position_greedy_movement`` is the candidate order: at each slot, place the
highest-final chunk that is allowed there and leaves a feasible completion.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

PRIOR_WEIGHT = 0.70
RRF_K = 60


def legacy_apply_bounded_movement(
    chunk_ids: list[str],
    final_scores: dict[str, float],
    max_movement: int = 3,
    confidence: dict[str, str] | None = None,
) -> list[str]:
    """Verbatim cb67d209 placement, keyed by chunk id instead of ChunkData."""
    n = len(chunk_ids)
    prior_rank = {cid: i for i, cid in enumerate(chunk_ids)}
    scored = sorted(chunk_ids, key=lambda cid: final_scores.get(cid, 0.0), reverse=True)
    if confidence is None:
        confidence = {}
    result: list[str | None] = [None] * n
    placed: set[int] = set()
    placed_chunks: set[str] = set()
    for cid in scored:
        old_pos = prior_rank.get(cid, n - 1)
        new_pos = scored.index(cid)
        movement = abs(new_pos - old_pos)
        conf = confidence.get(cid, "medium")
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
            result[new_pos] = cid
            placed.add(new_pos)
            placed_chunks.add(cid)
    remaining = [cid for cid in chunk_ids if cid not in placed_chunks]
    for i in range(n):
        if result[i] is None and remaining:
            result[i] = remaining.pop(0)
    return [cid for cid in result if cid is not None]


def _windows(
    chunk_ids: list[str], max_movement: int, confidence: dict[str, str]
) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    n = len(chunk_ids)
    for i, cid in enumerate(chunk_ids):
        k = max_movement if confidence.get(cid, "medium") == "high" else min(max_movement, 2)
        windows.append((max(0, i - k), min(n - 1, i + k)))
    return windows


def _feasible(remaining: set[int], start: int, windows: list[tuple[int, int]], n: int) -> bool:
    pool = set(remaining)
    for pos in range(start, n):
        eligible = [i for i in pool if windows[i][0] <= pos <= windows[i][1]]
        if not eligible:
            return False
        pick = min(eligible, key=lambda i: (windows[i][1], i))
        pool.remove(pick)
    return True


def position_greedy_movement(
    chunk_ids: list[str],
    final_scores: dict[str, float],
    max_movement: int = 3,
    confidence: dict[str, str] | None = None,
) -> list[str]:
    """Place, left to right, the best legal chunk that leaves a feasible rest."""
    n = len(chunk_ids)
    if n <= 1:
        return list(chunk_ids)
    if confidence is None:
        confidence = {cid: "high" for cid in chunk_ids}
    windows = _windows(chunk_ids, max_movement, confidence)
    remaining = set(range(n))
    order: list[int] = []
    for pos in range(n):
        eligible = [i for i in remaining if windows[i][0] <= pos <= windows[i][1]]
        eligible.sort(key=lambda i: (-final_scores.get(chunk_ids[i], 0.0), i))
        chosen = None
        for cand in eligible:
            if _feasible(remaining - {cand}, pos + 1, windows, n):
                chosen = cand
                break
        if chosen is None:
            raise RuntimeError("no feasible chunk for a windowed ranking")
        order.append(chosen)
        remaining.remove(chosen)
    return [chunk_ids[i] for i in order]


def fuse_floor(priors: list[float], ce: list[float], prior_weight: float = PRIOR_WEIGHT) -> list[float]:
    """Current fusion: prior divided by max(1, max prior), ce by max |ce|."""
    max_prior = max(1.0, max(priors, default=0.0))
    max_ce = max((abs(s) for s in ce), default=0.0) or 1.0
    return [
        prior_weight * (p / max_prior) + (1.0 - prior_weight) * (c / max_ce)
        for p, c in zip(priors, ce)
    ]


def fuse_no_floor(priors: list[float], ce: list[float], prior_weight: float = PRIOR_WEIGHT) -> list[float]:
    """Same weights, but the prior scale is the max prior in the set."""
    max_prior = max(priors, default=0.0) or 1.0
    max_ce = max((abs(s) for s in ce), default=0.0) or 1.0
    return [
        prior_weight * (p / max_prior) + (1.0 - prior_weight) * (c / max_ce)
        for p, c in zip(priors, ce)
    ]


def fuse_minmax(priors: list[float], ce: list[float], prior_weight: float = PRIOR_WEIGHT) -> list[float]:
    """Min-max both signals inside the candidate set, then apply the weights."""
    def _mm(values: list[float]) -> list[float]:
        lo, hi = min(values), max(values)
        if hi == lo:
            return [0.0 for _ in values]
        return [(v - lo) / (hi - lo) for v in values]

    pn, cn = _mm(priors), _mm(ce)
    return [prior_weight * p + (1.0 - prior_weight) * c for p, c in zip(pn, cn)]


def fuse_rrf(priors: list[float], ce: list[float], prior_weight: float = PRIOR_WEIGHT) -> list[float]:
    """Reciprocal-rank fusion of prior rank and cross-encoder rank."""
    def _ranks(values: list[float]) -> list[int]:
        order = sorted(range(len(values)), key=lambda i: (-values[i], i))
        ranks = [0] * len(values)
        for rank, idx in enumerate(order, start=1):
            ranks[idx] = rank
        return ranks

    pr, cr = _ranks(priors), _ranks(ce)
    return [
        prior_weight / (RRF_K + p) + (1.0 - prior_weight) / (RRF_K + c)
        for p, c in zip(pr, cr)
    ]


def ndcg_at(grades: list[int], k: int) -> float:
    def _dcg(vals: list[int]) -> float:
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(vals[:k]))

    ideal = _dcg(sorted(grades, reverse=True))
    if ideal == 0.0:
        return 0.0
    return _dcg(grades) / ideal


def precision_at(grades: list[int], k: int) -> float:
    head = grades[:k]
    if not head:
        return 0.0
    return sum(1 for g in head if g > 0) / len(head)


def inversions(order: list[str], finals: dict[str, float]) -> int:
    count = 0
    for i, left in enumerate(order):
        for right in order[i + 1 :]:
            if finals[left] < finals[right]:
                count += 1
    return count


def mean_displacement(prior: list[str], order: list[str]) -> float:
    pos = {cid: i for i, cid in enumerate(order)}
    if not prior:
        return 0.0
    return sum(abs(pos[cid] - i) for i, cid in enumerate(prior)) / len(prior)


def prior_order(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Descending prior, then chunk id. Ties in the live prior order are not recoverable."""
    return sorted(chunks, key=lambda c: (-float(c["prior_score"]), c["chunk_id"]))


def score_order(
    chunks: list[dict[str, Any]],
    order_ids: list[str],
) -> dict[str, float]:
    by_id = {c["chunk_id"]: c for c in chunks}
    grades = [int(by_id[cid]["relevance"]) for cid in order_ids]
    finals = {c["chunk_id"]: float(c["final_score"]) for c in chunks}
    prior_ids = [c["chunk_id"] for c in prior_order(chunks)]
    return {
        "ndcg5": ndcg_at(grades, 5),
        "p3": precision_at(grades, 3),
        "rel1": float(grades[0]) if grades else 0.0,
        "inversions": float(inversions(order_ids, finals)),
        "displacement": mean_displacement(prior_ids, order_ids),
    }


def macro(rows: list[dict[str, float]]) -> dict[str, float]:
    keys = rows[0].keys()
    n = len(rows)
    return {k: sum(r[k] for r in rows) / n for k in keys}


Fusion = Callable[[list[float], list[float]], list[float]]
Mover = Callable[[list[str], dict[str, float]], list[str]]
