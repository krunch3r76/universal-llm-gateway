"""BM25 sparse sidecar merged with dense results via mini-RRF."""

from __future__ import annotations

from typing import Any

import chromadb

from services.rag.fts_index import FtsIndex
from services.rag.search_scope.prefix_filter import apply_source_prefix_filter_with_ids

__all__ = ["apply_bm25_sidecar"]

_BM25_RRF_K = 20


def apply_bm25_sidecar(
    ids: list[str],
    chunks: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
    distances: list[float],
    query: str,
    fts: FtsIndex,
    collection: chromadb.Collection,
    source_prefixes: list[str] | None,
    *,
    bm25_limit: int = 30,
    rrf_k: int = _BM25_RRF_K,
) -> tuple[
    list[str],
    list[str],
    list[dict[str, str | int | float | bool]],
    list[float],
    int,
]:
    """Merge BM25 results into the dense candidate set via mini-RRF."""
    try:
        if source_prefixes:
            bm25_hits = fts.search_scoped(query, source_prefixes, limit=bm25_limit)
        else:
            bm25_hits = fts.search(query, limit=bm25_limit)
    except Exception:
        return ids, chunks, metadatas, distances, 0

    if not bm25_hits:
        return ids, chunks, metadatas, distances, 0

    dense_set = set(ids)

    rrf_scores: dict[str, float] = {}
    for rank, cid in enumerate(ids):
        rrf_scores[cid] = 1.0 / (rrf_k + rank + 1)

    bm25_only_ids: list[str] = []
    bm25_hit_count = 0
    for bm25_rank, (cid, _score) in enumerate(bm25_hits):
        bm25_rrf = 1.0 / (rrf_k + bm25_rank + 1)
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + bm25_rrf
        if cid in dense_set:
            bm25_hit_count += 1
        else:
            bm25_only_ids.append(cid)

    if not bm25_only_ids:
        combined = sorted(
            zip(ids, chunks, metadatas, distances, strict=True),
            key=lambda t: rrf_scores.get(t[0], 0.0),
            reverse=True,
        )
        return (
            [t[0] for t in combined],
            [t[1] for t in combined],
            [t[2] for t in combined],
            [t[3] for t in combined],
            bm25_hit_count,
        )

    try:
        fetched = collection.get(ids=bm25_only_ids, include=["documents", "metadatas"])
    except Exception:
        return ids, chunks, metadatas, distances, bm25_hit_count

    fetched_ids: list[str] = fetched.get("ids") or []
    fetched_docs_raw = fetched.get("documents")
    fetched_metas_raw = fetched.get("metadatas")
    fetched_docs = (
        fetched_docs_raw
        if isinstance(fetched_docs_raw, list)
        else [""] * len(fetched_ids)
    )
    if len(fetched_docs) < len(fetched_ids):
        fetched_docs = fetched_docs + ([""] * (len(fetched_ids) - len(fetched_docs)))
    else:
        fetched_docs = fetched_docs[: len(fetched_ids)]
    fetched_metas_list = (
        fetched_metas_raw
        if isinstance(fetched_metas_raw, list)
        else [{}] * len(fetched_ids)
    )
    if len(fetched_metas_list) < len(fetched_ids):
        fetched_metas_list = fetched_metas_list + (
            [{}] * (len(fetched_ids) - len(fetched_metas_list))
        )
    else:
        fetched_metas_list = fetched_metas_list[: len(fetched_ids)]

    tail_distance = max(distances) * 1.1 if distances else 1.0

    all_ids = list(ids)
    all_chunks = list(chunks)
    all_metadatas = list(metadatas)
    all_distances = list(distances)

    fetched_map: dict[str, tuple[str, dict[str, Any]]] = {}
    for fid, doc, meta in zip(fetched_ids, fetched_docs, fetched_metas_list, strict=True):
        if not isinstance(meta, dict):
            continue
        fetched_map[fid] = (doc if isinstance(doc, str) else "", meta)
    for cid in bm25_only_ids:
        if cid in fetched_map:
            doc, meta = fetched_map[cid]
            all_ids.append(cid)
            all_chunks.append(doc or "")
            all_metadatas.append(meta)
            all_distances.append(tail_distance)
            bm25_hit_count += 1

    if source_prefixes:
        all_ids, all_chunks, all_metadatas, all_distances = (
            apply_source_prefix_filter_with_ids(
                ids=all_ids,
                chunks=all_chunks,
                metadatas=all_metadatas,
                distances=all_distances,
                source_prefixes=source_prefixes,
                top_k=len(all_ids),
            )
        )

    combined = sorted(
        zip(all_ids, all_chunks, all_metadatas, all_distances, strict=True),
        key=lambda t: rrf_scores.get(t[0], 0.0),
        reverse=True,
    )
    return (
        [t[0] for t in combined],
        [t[1] for t in combined],
        [t[2] for t in combined],
        [t[3] for t in combined],
        bm25_hit_count,
    )
