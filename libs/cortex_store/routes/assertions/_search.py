"""Hybrid search — FTS5 + vector similarity with CombMAX score fusion.

Falls back to FTS5-only when the embedding model is unavailable.
Compaction-pointer rows are stripped from results by default per
``todo:cortex-aggregate-compaction-filter``.
"""

from __future__ import annotations

from fastapi import Query

from ... import embeddings as cortex_embeddings
from ... import vector_store
from ...compaction import filter_compaction_pointers
from ...db import cortex_conn, decode_row, query
from ...models import (
    AssertionSearchItem,
    AssertionSearchResult,
)
from ._shared import _JSON_FIELDS, _log_search_access, logger, router

_SEARCH_COLS = (
    "a.id, a.entity_id, a.claim, a.confidence, a.confidence_score, "
    "a.evidence, a.evidence_uris, a.seeded_by, a.derivation_type, "
    "a.prospective_summary, a.events_json, a.superseded_by, "
    "a.entrenchment_score, a.observed_at, a.created_at"
)

_SEARCH_COLS_WITH_ENTITY = _SEARCH_COLS + ", e.name AS entity_name"


def _sanitize_fts_query(raw: str) -> str:
    """Wrap each whitespace-split token in FTS5 double-quote phrase syntax.

    Quote-wrapping disables all operator interpretation (hyphens, colons,
    parentheses, boolean keywords) structurally — no enumeration of special
    characters is required. Each token is quoted independently, giving
    implicit AND semantics across terms. Embedded double-quotes are escaped
    as "" per the FTS5 phrase rules.
    """
    tokens = raw.strip().split()
    if not tokens:
        return ""
    return " ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens)


def _fts_search(
    conn: object,
    sanitized: str,
    superseded: bool,
    entity_type: str | None,
    limit: int,
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
    sql = (
        f"SELECT {_SEARCH_COLS_WITH_ENTITY}, rank "
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
    try:
        query_embedding = cortex_embeddings.embed_query(query_text)
        return vector_store.search_similar(query_embedding, n_results=n_results)
    except Exception:
        logger.warning("Vector search failed — degrading to FTS-only", exc_info=True)
        return []


def _combmax_fuse(
    fts_rows: list[dict],
    vector_results: list[dict],
    limit: int,
) -> tuple[list[dict], str]:
    """Fuse FTS5 and vector results via CombMAX score fusion.

    Returns (merged_items, search_mode) where search_mode is 'hybrid' or 'fulltext'.
    """
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


@router.get("/search", response_model=AssertionSearchResult)
def search_assertions(
    q: str = Query(..., min_length=1, description="Search query"),
    superseded: bool = Query(False, description="Include superseded assertions"),
    entity_type: str | None = Query(None, description="Filter to entity type"),
    limit: int = Query(20, ge=1, le=100),
    include_compaction_pointers: bool = Query(
        False,
        description=(
            "When true, include compaction-pointer assertions in results. "
            "Default false — pointers are bookkeeping rows that pollute "
            "topical search (todo:cortex-aggregate-compaction-filter)."
        ),
    ),
) -> AssertionSearchResult:
    """Hybrid search: FTS5 + vector similarity with CombMAX score fusion.

    Falls back to FTS5-only when the embedding model is unavailable.
    """
    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return AssertionSearchResult(query=q, items=[], total=0, search_mode="fulltext")

    # Over-fetch when filtering pointers so the returned count still approaches
    # `limit` after exclusion. 4× covers the typical pointer-density worst case
    # (manually-compacted entity dominates a focused query). The vector branch
    # mirrors the over-fetch so fusion has comparable candidate pools.
    fetch_multiplier = 4 if not include_compaction_pointers else 2

    with cortex_conn() as conn:
        fts_rows = _fts_search(
            conn, sanitized, superseded, entity_type, limit * fetch_multiplier
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

    if vector_only_ids:
        with cortex_conn() as conn:
            placeholders = ",".join("?" for _ in vector_only_ids)
            sql = (
                f"SELECT {_SEARCH_COLS_WITH_ENTITY} "
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

    # todo:cortex-aggregate-compaction-filter — strip compaction-pointer
    # rows from search results by default. Done after fusion so vector-only
    # hits (whose claim text was hydrated above) are filtered too.
    fused, _ = filter_compaction_pointers(
        fused, include_compaction_pointers=include_compaction_pointers
    )
    fused = fused[:limit]

    items: list[AssertionSearchItem] = []
    for item in fused:
        decoded = decode_row(item, _JSON_FIELDS)
        try:
            items.append(AssertionSearchItem(**decoded))
        except Exception:
            logger.warning(
                "Skipping assertion %s — deserialization failed",
                item.get("id"),
                exc_info=True,
            )

    _log_search_access(items)

    return AssertionSearchResult(
        query=q, items=items, total=len(items), search_mode=search_mode
    )


__all__ = ["_sanitize_fts_query", "search_assertions"]
