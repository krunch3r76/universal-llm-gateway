"""Deterministic operator partition across pool members."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def _operator_key(run_seed: int, doc_id: str, chunk_id: str) -> str:
    payload = f"{run_seed}:{doc_id}:{chunk_id}".encode()
    return hashlib.sha256(payload).hexdigest()


def _chunk_text(chunk: Any) -> str:
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, Mapping):
        for key in ("text", "content", "operator_statement", "body"):
            val = chunk.get(key)
            if val is not None and str(val).strip() != "":
                return str(val)
    return ""


def _first_non_empty(meta: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        val = meta.get(k)
        if val is None:
            continue
        s = str(val)
        if s.strip() != "":
            return s
    return None


def _coerce_operator_item(item: Any, index: int = 0) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        d = dict(item)
        d["doc_id"] = _first_non_empty(d, ("doc_id", "source_hash", "source", "file_path")) or f"doc_{index}"
        d["chunk_id"] = _first_non_empty(d, ("chunk_id", "chunk_hash", "id", "chunk_index")) or str(index)
        return d
    if isinstance(item, str):
        if ":" in item and not any(ch.isspace() for ch in item):
            doc_id, chunk_id = item.split(":", 1)
            return {"doc_id": doc_id, "chunk_id": chunk_id}
        digest = hashlib.sha256(item.encode("utf-8")).hexdigest()[:12]
        return {"doc_id": f"str_{digest}", "chunk_id": "0", "text": item}
    return None


def _normalize_operators(operators: Any) -> list[dict[str, Any]]:
    if isinstance(operators, Mapping):
        if "chunks" in operators:
            chunks = list(operators.get("chunks") or [])
            metadata = list(operators.get("metadata") or [])
            out: list[dict[str, Any]] = []
            for i, chunk in enumerate(chunks):
                meta_raw = metadata[i] if i < len(metadata) else {}
                meta = meta_raw if isinstance(meta_raw, Mapping) else {}
                doc_id = _first_non_empty(meta, ("doc_id", "source_hash", "source", "file_path")) or f"doc_{i}"
                chunk_id = _first_non_empty(meta, ("chunk_id", "chunk_hash", "id", "chunk_index")) or str(i)
                out.append({"doc_id": doc_id, "chunk_id": chunk_id, "text": _chunk_text(chunk)})
            return out
        logger.warning("operator_split_v1: mapping operators missing chunks key")
        return []
    if isinstance(operators, (list, tuple, set)):
        out: list[dict[str, Any]] = []
        for i, item in enumerate(operators):
            coerced = _coerce_operator_item(item, i)
            if coerced is not None:
                out.append(coerced)
        return out
    if operators not in (None, ""):
        logger.warning("operator_split_v1: unsupported operators type %s", type(operators).__name__)
    return []


def _format_operator_entry(op: dict[str, Any]) -> str:
    text = op.get("text") or op.get("content")
    if isinstance(text, str) and text.strip():
        return f"[{op.get('doc_id')}:{op.get('chunk_id')}] {text}"
    return f"{op.get('doc_id')}:{op.get('chunk_id')}"


def _dedupe_operators(operators: Any) -> list[dict[str, Any]]:
    normalized = _normalize_operators(operators)
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for op in normalized:
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


def operator_dispersion(subsets: list[list[Any]]) -> float:
    """1 minus mean pairwise Jaccard overlap of doc_id sets."""
    doc_sets: list[set[str]] = []
    for subset in subsets:
        doc_ids: set[str] = set()
        for idx, op in enumerate(subset):
            coerced = _coerce_operator_item(op, idx)
            if coerced is not None:
                doc_ids.add(str(coerced.get("doc_id", "")))
        doc_sets.append(doc_ids)
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
    operators: Any,
    pool_manifest: list[dict[str, Any]],
    run_seed: int,
) -> dict[str, Any]:
    """Partition operators into n disjoint subsets; emit pool_models list."""
    manifest = pool_manifest or []
    n = len(manifest)
    deduped = _dedupe_operators(operators)
    ordered = sorted(
        deduped,
        key=lambda op: _operator_key(run_seed, str(op.get("doc_id", "")), str(op.get("chunk_id", ""))),
    )
    subsets: list[list[dict[str, Any]]] = [[] for _ in range(max(n, 1))]
    for idx, op in enumerate(ordered):
        if n == 0:
            break
        subsets[idx % n].append(op)
    pool_models = [
        str(entry.get("model", "")) if isinstance(entry, Mapping) else str(entry) for entry in manifest
    ]
    active_subsets = subsets[:n] if n else []
    operator_subsets = [[_format_operator_entry(op) for op in subset] for subset in active_subsets]
    return {
        "operator_subsets": operator_subsets,
        "pool_models": pool_models,
        "operator_dispersion": operator_dispersion(active_subsets),
    }
