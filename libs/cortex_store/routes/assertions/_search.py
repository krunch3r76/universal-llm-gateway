"""Hybrid search — FTS5 + vector similarity with CombMAX score fusion.

Falls back to FTS5-only when the embedding model is unavailable.
Compaction-pointer rows are stripped from results by default per
``todo:cortex-aggregate-compaction-filter``.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

import httpx
from fastapi import Query

from ... import embeddings as cortex_embeddings
from ... import vector_store
from ...compaction import filter_compaction_pointers
from ...db import cortex_conn, decode_row, query
from ...event_publisher import cortex_search_failed, cortex_search_vector_degraded
from ...models import (
    AssertionSearchItem,
    AssertionSearchResult,
    AssertionSearchSummaryItem,
)
from ._shared import (
    _JSON_FIELDS,
    _SEARCH_COLS_WITH_ENTITY,
    _SEARCH_SUMMARY_COLS_WITH_ENTITY,
    _log_search_access,
    _truncate_claim,
    logger,
    router,
)


def _sanitize_fts_query(raw: str) -> str:
    """Wrap each whitespace-split token in FTS5 double-quote phrase syntax."""
    tokens = raw.strip().split()
    if not tokens:
        return ""
    return " ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens)


def _search_cols(*, intent: Literal["summary", "full"]) -> str:
    return (
        _SEARCH_SUMMARY_COLS_WITH_ENTITY
        if intent == "summary"
        else _SEARCH_COLS_WITH_ENTITY
    )


def _fts_search(
    conn: object,
    sanitized: str,
    superseded: bool,
    entity_type: str | None,
    limit: int,
    *,
    intent: Literal["summary", "full"],
) -> list[dict]:
    """Run the FTS5 branch of hybrid search."""
    clauses: list[str] = ["f.indexed_text MATCH ?"]
    params: list[str | int] = [sanitized]

    if not superseded:
        clauses.append("a.superseded_by IS NULL")
    if entity_type:
        clauses.append("e.type = ?")
        params.append(entity_type)

    where = " AND ".join(clauses)
    cols = _search_cols(intent=intent)
    sql = (
        f"SELECT {cols}, rank "
        "FROM assertions_fts f "
        "JOIN assertions a ON a.id = f.assertion_id "
        "JOIN entities e ON a.entity_id = e.id "
        f"WHERE {where} "
        "ORDER BY rank "
        "LIMIT ?"
    )
    params.append(limit)
    return query(conn, sql, tuple(params))  # type: ignore[arg-type]


def _vector_search(query_text: str, n_results: int) -> list[dict]:
    """Run the vector branch of hybrid search. Returns [] on failure."""
    if not cortex_embeddings.is_configured() or not vector_store.is_initialized():
        return []
    t0 = time.monotonic()
    try:
        query_embedding = cortex_embeddings.embed_query(query_text)
        return vector_store.search_similar(query_embedding, n_results=n_results)
    except Exception as exc:
        duration_s = round(time.monotonic() - t0, 3)
        reason = (
            "vector_embed_timeout"
            if isinstance(exc, httpx.TimeoutException)
            else "vector_error"
        )
        cortex_search_vector_degraded(
            reason=reason,
            exc_type=type(exc).__name__,
            q_len=len(query_text),
            duration_s=duration_s,
        )
        return []


def _combmax_fuse(
    fts_rows: list[dict],
    vector_results: list[dict],
    limit: int,
) -> tuple[list[dict], str]:
    """Fuse FTS5 and vector results via CombMAX score fusion."""
    if not vector_results:
        max_abs_rank = (
            max((abs(r.get("rank", 0)) for r in fts_rows), default=1.0) or 1.0
        )
        items: list[dict] = []
        for row in fts_rows:
            raw_rank = row.get("rank", 0)
            bm25_norm = abs(raw_rank) / max_abs_rank
            items.append(
                {
                    **{k: v for k, v in row.items() if k != "rank"},
                    "rank": raw_rank,
                    "bm25_score": round(bm25_norm, 4),
                    "cosine_similarity": None,
                    "combmax_score": round(bm25_norm, 4),
                    "retrieval_source": "fts",
                }
            )
        return items[:limit], "fulltext"

    max_abs_rank = max((abs(r.get("rank", 0)) for r in fts_rows), default=1.0) or 1.0

    merged: dict[int, dict] = {}

    for row in fts_rows:
        aid = row["id"]
        raw_rank = row.get("rank", 0)
        bm25_norm = abs(raw_rank) / max_abs_rank
        merged[aid] = {
            **{k: v for k, v in row.items() if k != "rank"},
            "rank": raw_rank,
            "bm25_score": round(bm25_norm, 4),
            "cosine_similarity": None,
            "combmax_score": round(bm25_norm, 4),
            "retrieval_source": "fts",
        }

    for vr in vector_results:
        aid = vr["assertion_id"]
        cosine_sim = vr.get("cosine_similarity", 0.0)
        if aid in merged:
            existing = merged[aid]
            existing["cosine_similarity"] = round(cosine_sim, 4)
            existing["combmax_score"] = round(
                max(existing.get("bm25_score", 0.0), cosine_sim), 4
            )
            existing["retrieval_source"] = "both"
        else:
            merged[aid] = {
                "id": aid,
                "entity_id": vr.get("entity_id"),
                "entity_name": None,
                "bm25_score": None,
                "cosine_similarity": round(cosine_sim, 4),
                "combmax_score": round(cosine_sim, 4),
                "retrieval_source": "vector",
                "rank": None,
            }

    sorted_items = sorted(
        merged.values(),
        key=lambda x: x.get("combmax_score", 0.0),
        reverse=True,
    )
    return sorted_items[:limit], "hybrid"


def _hydrate_vector_only(
    fused: list[dict],
    vector_only_ids: set[int],
    *,
    intent: Literal["summary", "full"],
) -> None:
    if not vector_only_ids:
        return
    base_cols = (
        _SEARCH_SUMMARY_COLS_WITH_ENTITY
        if intent == "summary"
        else _SEARCH_COLS_WITH_ENTITY
    )
    with cortex_conn() as conn:
        placeholders = ",".join("?" for _ in vector_only_ids)
        sql = (
            f"SELECT {base_cols} "
            "FROM assertions a "
            "JOIN entities e ON a.entity_id = e.id "
            f"WHERE a.id IN ({placeholders})"
        )
        hydrated = query(conn, sql, tuple(vector_only_ids))
        hydrated_map = {r["id"]: r for r in hydrated}
        for item in fused:
            if item["id"] in hydrated_map:
                row = hydrated_map[item["id"]]
                for key, val in row.items():
                    if key not in (
                        "bm25_score",
                        "cosine_similarity",
                        "combmax_score",
                        "retrieval_source",
                        "rank",
                    ):
                        item[key] = val


def _summary_items(fused: list[dict]) -> list[AssertionSearchSummaryItem]:
    items: list[AssertionSearchSummaryItem] = []
    for item in fused:
        decoded = decode_row(item, _JSON_FIELDS)
        claim = decoded.get("claim") or ""
        try:
            items.append(
                AssertionSearchSummaryItem(
                    id=decoded["id"],
                    entity_id=decoded.get("entity_id"),
                    entity_name=decoded.get("entity_name"),
                    claim=_truncate_claim(str(claim)),
                    confidence=decoded["confidence"],
                    review_status=decoded.get("review_status"),
                    combmax_score=decoded.get("combmax_score"),
                    retrieval_source=decoded.get("retrieval_source") or "fts",
                )
            )
        except Exception:
            logger.error(
                "Skipping assertion %s — summary deserialization failed",
                item.get("id"),
                exc_info=True,
            )
    return items


def _full_items(fused: list[dict]) -> list[AssertionSearchItem]:
    items: list[AssertionSearchItem] = []
    for item in fused:
        decoded = decode_row(item, _JSON_FIELDS)
        try:
            items.append(AssertionSearchItem(**decoded))
        except Exception:
            logger.error(
                "Skipping assertion %s — deserialization failed",
                item.get("id"),
                exc_info=True,
            )
    return items


@router.get("/search", response_model=AssertionSearchResult)
def search_assertions(
    q: Annotated[str, Query(min_length=1, description="Search query")],
    superseded: Annotated[
        bool, Query(description="Include superseded assertions")
    ] = False,
    entity_type: Annotated[
        str | None, Query(description="Filter to entity type")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    intent: Annotated[
        Literal["summary", "full"],
        Query(
            description=(
                "Response projection. summary (default): compact hits; "
                "full: assertion-detail shape with enrichment fields."
            )
        ),
    ] = "summary",
    include_compaction_pointers: Annotated[
        bool,
        Query(
            description=(
                "When true, include compaction-pointer assertions in results. "
                "Default false — pointers are bookkeeping rows that pollute "
                "topical search (todo:cortex-aggregate-compaction-filter)."
            )
        ),
    ] = False,
) -> AssertionSearchResult:
    """Hybrid search: FTS5 + vector similarity with CombMAX score fusion."""
    try:
        return _search_assertions_impl(
            q=q,
            superseded=superseded,
            entity_type=entity_type,
            limit=limit,
            intent=intent,
            include_compaction_pointers=include_compaction_pointers,
        )
    except Exception as exc:
        cortex_search_failed(
            exc_type=type(exc).__name__,
            detail=str(exc),
            q_len=len(q),
            intent=intent,
        )
        raise


def _search_assertions_impl(
    *,
    q: str,
    superseded: bool,
    entity_type: str | None,
    limit: int,
    intent: Literal["summary", "full"],
    include_compaction_pointers: bool,
) -> AssertionSearchResult:
    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return AssertionSearchResult(
            query=q,
            intent=intent,
            items=[],
            total=0,
            search_mode="fulltext",
        )

    fetch_multiplier = 4 if not include_compaction_pointers else 2

    with cortex_conn() as conn:
        fts_rows = _fts_search(
            conn,
            sanitized,
            superseded,
            entity_type,
            limit * fetch_multiplier,
            intent=intent,
        )

    vector_results = _vector_search(q, n_results=limit * fetch_multiplier)

    fused, search_mode = _combmax_fuse(
        fts_rows, vector_results, limit * fetch_multiplier
    )

    vector_only_ids: set[int] = set()
    if search_mode == "hybrid":
        vector_only_ids = {
            item["id"] for item in fused if item.get("retrieval_source") == "vector"
        }

    _hydrate_vector_only(fused, vector_only_ids, intent=intent)

    fused, _ = filter_compaction_pointers(
        fused, include_compaction_pointers=include_compaction_pointers
    )
    fused = fused[:limit]

    if intent == "summary":
        summary = _summary_items(fused)
        _log_search_access(summary)
        return AssertionSearchResult(
            query=q,
            intent=intent,
            items=summary,
            total=len(summary),
            search_mode=search_mode,
        )

    full = _full_items(fused)
    _log_search_access(full)
    return AssertionSearchResult(
        query=q,
        intent=intent,
        items=full,
        total=len(full),
        search_mode=search_mode,
    )


__all__ = ["_sanitize_fts_query", "search_assertions"]
