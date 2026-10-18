"""Deterministic operator partition across pool members."""

from __future__ import annotations

import hashlib
from typing import Any


def _operator_key(run_seed: int, doc_id: str, chunk_id: str) -> str:
    payload = f"{run_seed}:{doc_id}:{chunk_id}".encode()
    return hashlib.sha256(payload).hexdigest()


def _dedupe_operators(operators: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for op in operators:
        key = (str(op.get("doc_id", "")), str(op.get("chunk_id", "")))
        if key in seen:
            continue
        seen.add(key)
        out.append(op)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def operator_dispersion(subsets: list[list[dict[str, Any]]]) -> float:
    """1 minus mean pairwise Jaccard overlap of doc_id sets."""
    doc_sets = [{str(op.get("doc_id", "")) for op in subset} for subset in subsets]
    if len(doc_sets) < 2:
        return 1.0
    overlaps: list[float] = []
    for i in range(len(doc_sets)):
        for j in range(i + 1, len(doc_sets)):
            overlaps.append(_jaccard(doc_sets[i], doc_sets[j]))
    if not overlaps:
        return 1.0
    mean_overlap = sum(overlaps) / len(overlaps)
    return max(0.0, min(1.0, 1.0 - mean_overlap))


def split_operators(
    operators: list[dict[str, Any]],
    pool_manifest: list[dict[str, Any]],
    run_seed: int,
) -> dict[str, Any]:
    """Partition operators into n disjoint subsets; emit pool_models list."""
    n = len(pool_manifest)
    deduped = _dedupe_operators(list(operators))
    ordered = sorted(
        deduped,
        key=lambda op: _operator_key(run_seed, str(op.get("doc_id", "")), str(op.get("chunk_id", ""))),
    )
    subsets: list[list[dict[str, Any]]] = [[] for _ in range(max(n, 1))]
    for idx, op in enumerate(ordered):
        if n == 0:
            break
        subsets[idx % n].append(op)
    pool_models = [str(entry.get("model", "")) for entry in pool_manifest]
    operator_subsets = [
        [f"{op.get('doc_id')}:{op.get('chunk_id')}" for op in subset] for subset in subsets[:n]
    ]
    return {
        "operator_subsets": operator_subsets,
        "pool_models": pool_models,
        "operator_dispersion": operator_dispersion(subsets[:n]),
    }
