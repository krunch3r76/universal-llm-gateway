"""Hybrid search — FTS5 + vector similarity with tier0 entity cards + tier1 RRF.

Falls back to FTS5-only when the embedding model is unavailable.
Compaction-pointer rows are stripped from results by default per
``todo:cortex-aggregate-compaction-filter``.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

import httpx
from fastapi import Query
from openapi_mcp.binding import x_mcp

from ... import embeddings as cortex_embeddings
from ... import vector_store
from ...compaction import filter_compaction_pointers
from ...db import cortex_conn, decode_row, query
from ...entity_aliases import live_alias_rows
from ...entity_discover import (
    _aliases_for_entity,
    _group_aliases_by_entity,
    _identity_match_and_score,
    _normalize_tokens,
)
from ...entity_fts import (
    _STRICT_IDENTITY_COLS,
    _fetch_fts_lane,
    _type_prior,
    entities_fts_table_exists,
)
from ...event_publisher import cortex_search_failed, cortex_search_vector_degraded
from ...models import (
    ActionHint,
    AssertionSearchItem,
    AssertionSearchResult,
    AssertionSearchSummaryItem,
)
from ...models.search import (
    EntitySearchItem,
    EntitySearchSummaryItem,
    entity_search_action_hints,
    fulltext_search_action_hints,
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

_RRF_K = 20
_LOOSE_ENTITY_RRF_WEIGHT = 0.5
_ENTITY_KEY_PREFIX = "entity:"
_ASSERTION_KEY_PREFIX = "assertion:"
_STRICT_FETCH_CAP = 50
_TIER0_BM25_FRACTION = 0.5


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


def _rrf_fuse(
    ranked_lists: list[tuple[list[str], float]],
    *,
    k: int = _RRF_K,
) -> dict[str, float]:
    """Reciprocal rank fusion: score(key) = sum_l w_l / (k + rank_l)."""
    scores: dict[str, float] = {}
    for keys, weight in ranked_lists:
        for rank, key in enumerate(keys):
            scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)
    return scores


def _active_assertion_count(conn: object, entity_id: str) -> int:
    rows = query(
        conn,
        "SELECT COUNT(*) AS cnt FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL",
        (entity_id,),
    )
    return int(rows[0]["cnt"]) if rows else 0


def _entity_context(
    conn: object,
    entity_ids: set[str],
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    if not entity_ids:
        return {}, {}
    alias_index = live_alias_rows(conn)
    aliases_by_entity = _group_aliases_by_entity(alias_index)
    entity_rows = {
        str(r["id"]): r
        for r in query(
            conn,
            f"SELECT id, type, name, description, source_uri, aliases, lifecycle "
            f"FROM entities WHERE id IN ({','.join('?' * len(entity_ids))})",
            tuple(entity_ids),
        )
    }
    return aliases_by_entity, entity_rows


def _score_entity_fts_rows(
    conn: object,
    fts_rows: list[dict[str, Any]],
    query_tokens: list[str],
    *,
    aliases_by_entity: dict[str, list[str]] | None = None,
    entity_rows: dict[str, dict[str, Any]] | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """Return (prior_weighted_bm25, hit_dict) sorted descending by weighted score."""
    if not fts_rows:
        return []
    ids = {str(r["entity_id"]) for r in fts_rows}
    if aliases_by_entity is None or entity_rows is None:
        aliases_by_entity, entity_rows = _entity_context(conn, ids)

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in fts_rows:
        eid = str(row["entity_id"])
        ent = entity_rows.get(eid)
        if not ent:
            continue
        lifecycle = ent.get("lifecycle")
        if lifecycle in ("merged", "deprecated", "reaped"):
            continue
        name = str(ent.get("name") or "")
        source_uri = str(ent.get("source_uri") or "")
        description = str(ent.get("description") or "")
        entity_aliases = _aliases_for_entity(eid, ent, aliases_by_entity)
        _identity_match, overlap_score, _specificity = _identity_match_and_score(
            query_tokens,
            entity_id=eid,
            name=name,
            aliases=entity_aliases,
            source_uri=source_uri,
            description=description,
        )
        if overlap_score <= 0:
            continue
        raw_rank = float(row.get("rank") or 0.0)
        etype = str(row.get("etype") or ent.get("type") or "")
        weighted = abs(raw_rank) * _type_prior(etype)
        hit = {
            "entity_id": eid,
            "entity_name": ent.get("name"),
            "entity_type": ent.get("type") or etype,
            "weighted_bm25": weighted,
            "retrieval_source": "entity",
        }
        scored.append((weighted, hit))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def _tier0_strict_entity_cards(
    conn: object,
    q: str,
    *,
    entity_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Strict-lane entity cards: prior-weighted BM25 >= 0.5×best, cap limit//2."""
    cap = limit // 2
    if cap <= 0:
        return []
    tokens = _normalize_tokens(q)
    if not tokens or not entities_fts_table_exists(conn):
        return []
    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return []

    strict_match = f"{_STRICT_IDENTITY_COLS}: {sanitized}"
    strict_rows = _fetch_fts_lane(
        conn, strict_match, entity_type=entity_type, cap=_STRICT_FETCH_CAP
    )
    scored = _score_entity_fts_rows(conn, strict_rows, tokens)
    if not scored:
        return []

    best_weighted = scored[0][0]
    threshold = _TIER0_BM25_FRACTION * best_weighted
    cards: list[dict[str, Any]] = []
    for weighted, hit in scored:
        if weighted < threshold:
            break
        if len(cards) >= cap:
            break
        eid = str(hit["entity_id"])
        cards.append(
            {
                "kind": "entity",
                "entity_id": eid,
                "entity_type": str(hit.get("entity_type") or ""),
                "entity_name": hit.get("entity_name"),
                "score": round(float(weighted), 4),
                "retrieval_source": "entity",
                "assertion_count": _active_assertion_count(conn, eid),
                "action_hints": entity_search_action_hints(entity_id=eid),
            }
        )
    return cards


def _loose_lane_entity_keys(
    conn: object,
    q: str,
    *,
    entity_type: str | None,
    strict_entity_ids: set[str],
    fetch_cap: int,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Loose-lane entities for tier1 RRF (weight 0.5), bm25 order."""
    tokens = _normalize_tokens(q)
    if not tokens or not entities_fts_table_exists(conn):
        return [], {}
    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return [], {}

    loose_rows = _fetch_fts_lane(
        conn, sanitized, entity_type=entity_type, cap=_STRICT_FETCH_CAP
    )
    loose_rows = [
        r for r in loose_rows if str(r["entity_id"]) not in strict_entity_ids
    ]
    scored = _score_entity_fts_rows(conn, loose_rows, tokens)
    hit_by_key: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for _weighted, hit in scored[:fetch_cap]:
        eid = str(hit["entity_id"])
        key = f"{_ENTITY_KEY_PREFIX}{eid}"
        keys.append(key)
        hit_by_key[key] = hit
    return keys, hit_by_key


def _hydrate_vector_only(
    fts_by_id: dict[int, dict[str, Any]],
    vector_results: list[dict],
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
        for row in hydrated:
            aid = int(row["id"])
            fts_by_id.setdefault(aid, {})
            for key, val in row.items():
                if key != "rank":
                    fts_by_id[aid][key] = val


def _tier1_rrf_items(
    conn: object,
    *,
    fts_rows: list[dict],
    vector_results: list[dict],
    loose_entity_keys: list[str],
    loose_entity_hits: dict[str, dict[str, Any]],
    limit: int,
    intent: Literal["summary", "full"],
) -> list[dict[str, Any]]:
    fts_by_id: dict[int, dict[str, Any]] = {}
    for row in fts_rows:
        aid = int(row["id"])
        fts_by_id[aid] = {k: v for k, v in row.items() if k != "rank"}
        fts_by_id[aid]["rank"] = row.get("rank")

    vector_by_id: dict[int, dict[str, Any]] = {}
    for vr in vector_results:
        aid = int(vr["assertion_id"])
        vector_by_id[aid] = vr

    vector_only_ids = set(vector_by_id) - set(fts_by_id)
    _hydrate_vector_only(fts_by_id, vector_results, vector_only_ids, intent=intent)

    fts_ranked = [
        f"{_ASSERTION_KEY_PREFIX}{int(row['id'])}" for row in fts_rows
    ]
    vector_ranked = sorted(
        vector_by_id.keys(),
        key=lambda aid: float(vector_by_id[aid].get("cosine_similarity") or 0.0),
        reverse=True,
    )
    vector_keys = [f"{_ASSERTION_KEY_PREFIX}{aid}" for aid in vector_ranked]

    ranked_lists: list[tuple[list[str], float]] = [
        (fts_ranked, 1.0),
        (vector_keys, 1.0),
    ]
    if loose_entity_keys:
        ranked_lists.append((loose_entity_keys, _LOOSE_ENTITY_RRF_WEIGHT))

    rrf_scores = _rrf_fuse(ranked_lists, k=_RRF_K)
    sorted_keys = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)

    items: list[dict[str, Any]] = []
    for key in sorted_keys:
        if len(items) >= limit:
            break
        score = round(rrf_scores[key], 6)
        if key.startswith(_ENTITY_KEY_PREFIX):
            hit = loose_entity_hits.get(key)
            if not hit:
                continue
            eid = str(hit["entity_id"])
            items.append(
                {
                    "kind": "entity",
                    "entity_id": eid,
                    "entity_type": str(hit.get("entity_type") or ""),
                    "entity_name": hit.get("entity_name"),
                    "score": score,
                    "retrieval_source": "entity",
                    "assertion_count": _active_assertion_count(conn, eid),
                    "action_hints": entity_search_action_hints(entity_id=eid),
                }
            )
            continue

        if not key.startswith(_ASSERTION_KEY_PREFIX):
            continue
        aid = int(key.removeprefix(_ASSERTION_KEY_PREFIX))
        base = fts_by_id.get(aid, {"id": aid})
        vr = vector_by_id.get(aid)
        raw_rank = base.get("rank")
        bm25_score = round(abs(float(raw_rank)), 4) if raw_rank is not None else None
        cosine = (
            round(float(vr.get("cosine_similarity")), 4) if vr is not None else None
        )
        if bm25_score is not None and cosine is not None:
            retrieval_source = "both"
        elif cosine is not None:
            retrieval_source = "vector"
        else:
            retrieval_source = "fts"

        item = {
            **{k: v for k, v in base.items() if k not in ("rank",)},
            "kind": "assertion",
            "id": aid,
            "score": score,
            "combmax_score": score,
            "bm25_score": bm25_score,
            "cosine_similarity": cosine,
            "retrieval_source": retrieval_source,
            "rank": raw_rank,
        }
        items.append(item)
    return items


def _summary_items(fused: list[dict]) -> list[AssertionSearchSummaryItem | EntitySearchSummaryItem]:
    items: list[AssertionSearchSummaryItem | EntitySearchSummaryItem] = []
    for item in fused:
        if item.get("kind") == "entity":
            try:
                items.append(
                    EntitySearchSummaryItem(
                        entity_id=item["entity_id"],
                        entity_type=item["entity_type"],
                        entity_name=item.get("entity_name"),
                        score=float(item["score"]),
                        assertion_count=int(item["assertion_count"]),
                        action_hints=item.get("action_hints"),
                    )
                )
            except Exception:
                logger.error(
                    "Skipping entity %s — summary deserialization failed",
                    item.get("entity_id"),
                    exc_info=True,
                )
            continue
        decoded = decode_row(item, _JSON_FIELDS)
        claim = decoded.get("claim") or ""
        try:
            score = decoded.get("score")
            items.append(
                AssertionSearchSummaryItem(
                    id=decoded["id"],
                    entity_id=decoded.get("entity_id"),
                    entity_name=decoded.get("entity_name"),
                    claim=_truncate_claim(str(claim)),
                    confidence=decoded["confidence"],
                    review_status=decoded.get("review_status"),
                    score=score,
                    combmax_score=score,
                    bm25_score=decoded.get("bm25_score"),
                    cosine_similarity=decoded.get("cosine_similarity"),
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


def _action_hints_for_search_mode(
    search_mode: str,
    *,
    query: str,
) -> list[ActionHint] | None:
    if search_mode != "fulltext":
        return None
    return fulltext_search_action_hints(query=query)


def _full_items(fused: list[dict]) -> list[AssertionSearchItem | EntitySearchItem]:
    items: list[AssertionSearchItem | EntitySearchItem] = []
    for item in fused:
        if item.get("kind") == "entity":
            try:
                items.append(
                    EntitySearchItem(
                        entity_id=item["entity_id"],
                        entity_type=item["entity_type"],
                        entity_name=item.get("entity_name"),
                        score=float(item["score"]),
                        assertion_count=int(item["assertion_count"]),
                        action_hints=item.get("action_hints"),
                    )
                )
            except Exception:
                logger.error(
                    "Skipping entity %s — deserialization failed",
                    item.get("entity_id"),
                    exc_info=True,
                )
            continue
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


@router.get(
    "/search", response_model=AssertionSearchResult, openapi_extra=x_mcp("search")
)
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
    """Hybrid search: tier0 entity cards + tier1 assertion RRF (k=20)."""
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
            action_hints=_action_hints_for_search_mode("fulltext", query=q),
        )

    fetch_multiplier = 4 if not include_compaction_pointers else 2
    fetch_limit = limit * fetch_multiplier

    with cortex_conn() as conn:
        tier0 = _tier0_strict_entity_cards(
            conn, q, entity_type=entity_type, limit=limit
        )
        strict_ids = {str(c["entity_id"]) for c in tier0}

        fts_rows = _fts_search(
            conn,
            sanitized,
            superseded,
            entity_type,
            fetch_limit,
            intent=intent,
        )
        loose_keys, loose_hits = _loose_lane_entity_keys(
            conn,
            q,
            entity_type=entity_type,
            strict_entity_ids=strict_ids,
            fetch_cap=fetch_limit,
        )

    vector_results = _vector_search(q, n_results=fetch_limit)
    search_mode = "hybrid" if vector_results else "fulltext"

    fts_rows, _ = filter_compaction_pointers(
        fts_rows, include_compaction_pointers=include_compaction_pointers
    )

    tier1_limit = max(0, limit - len(tier0))
    with cortex_conn() as conn:
        tier1 = _tier1_rrf_items(
            conn,
            fts_rows=fts_rows,
            vector_results=vector_results,
            loose_entity_keys=loose_keys,
            loose_entity_hits=loose_hits,
            limit=tier1_limit,
            intent=intent,
        )

    fused = tier0 + tier1

    if intent == "summary":
        summary = _summary_items(fused)
        _log_search_access(summary)
        return AssertionSearchResult(
            query=q,
            intent=intent,
            items=summary,
            total=len(summary),
            search_mode=search_mode,
            action_hints=_action_hints_for_search_mode(search_mode, query=q),
        )

    full = _full_items(fused)
    _log_search_access(full)
    return AssertionSearchResult(
        query=q,
        intent=intent,
        items=full,
        total=len(full),
        search_mode=search_mode,
        action_hints=_action_hints_for_search_mode(search_mode, query=q),
    )


__all__ = [
    "_rrf_fuse",
    "_sanitize_fts_query",
    "_tier0_strict_entity_cards",
    "search_assertions",
]
