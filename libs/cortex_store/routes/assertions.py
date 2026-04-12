from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException, Query, Response, status

from .. import embeddings as cortex_embeddings
from .. import vector_store
from ..action_hints import detect_expired_unresolved
from ..assertion_quality import validate_assertion
from ..belief_guard import (
    analyze_assertion_impact,
    guard_assertion_write,
)
from ..claim_hash import compute_claim_hash
from ..db import WRITE_LOCK, cortex_conn, decode_row, json_encode, query
from ..enrichment import (
    enrich_assertion,
    enrich_background,
    enrich_old_assertion_events,
    reindex_assertion_fts,
)
from ..entrenchment import compute_entrenchment
from ..graph_utils import check_contradictions
from ..models import (
    AssertionCreate,
    AssertionCreateResponse,
    AssertionItem,
    AssertionList,
    AssertionSearchItem,
    AssertionSearchResult,
    AssertionUpdate,
    ContradictionConflict,
    EnrichRequest,
    EnrichResponse,
    NearDuplicateWarning,
    SupersedeRequest,
    SupersedeResponse,
)
from ..near_dup import check_near_duplicate, record_near_duplicate

logger = logging.getLogger("cortex-api.assertions")


def _log_search_access(items: list) -> None:
    """Batch-log access for entities touched by search results (TTL reset for ephemeral)."""
    entity_ids = {
        getattr(item, "entity_id", None) or item.get("entity_id")
        for item in items
        if item
    }
    entity_ids.discard(None)
    if not entity_ids:
        return
    try:
        with WRITE_LOCK, cortex_conn() as conn:
            conn.executemany(
                "INSERT INTO entity_access_log "
                "(entity_id, agent, operation, source) VALUES (?, 'system', 'search', 'search')",
                [(eid,) for eid in entity_ids],
            )
            conn.commit()
    except Exception:
        logger.debug("Batch access log insert failed for search results")


def _embed_assertion_background(assertion_id: int, assertion_row: dict) -> None:
    """Compute and upsert assertion embedding in a daemon thread.

    Non-blocking: failures are logged and swallowed. The assertion remains
    valid and FTS-searchable even if embedding fails.
    """
    import threading

    if not cortex_embeddings.is_configured() or not vector_store.is_initialized():
        return

    def _run() -> None:
        try:
            text = vector_store.assertion_embedding_text(assertion_row)
            embeddings = cortex_embeddings.embed_texts([text])
            if embeddings:
                meta: dict = {}
                if assertion_row.get("entity_id"):
                    meta["entity_id"] = assertion_row["entity_id"]
                if assertion_row.get("confidence"):
                    meta["confidence"] = assertion_row["confidence"]
                if assertion_row.get("derivation_type"):
                    meta["derivation_type"] = assertion_row["derivation_type"]
                if assertion_row.get("entrenchment_score") is not None:
                    meta["entrenchment_score"] = float(
                        assertion_row["entrenchment_score"]
                    )
                if assertion_row.get("observed_at"):
                    meta["observed_at"] = assertion_row["observed_at"]
                vector_store.upsert_assertion_embedding(
                    assertion_id=assertion_id,
                    text=text,
                    embedding=embeddings[0],
                    metadata=meta,
                )
        except Exception:
            logger.warning(
                "Background embedding failed for assertion %d",
                assertion_id,
                exc_info=True,
            )

    t = threading.Thread(target=_run, daemon=True)
    t.start()


router = APIRouter(prefix="/assertions", tags=["assertions"])

_JSON_FIELDS = frozenset({"evidence_uris"})

_VALID_CONFIDENCE = {"confirmed", "believed", "suspected", "hypothesized"}

_ASSERTION_COLS = (
    "id, entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by, "
    "derivation_type, chunk_id, reasoning_summary, is_atomic, is_decontextualized, "
    "observed_at, valid_from, valid_until, superseded_by, "
    "review_status, reviewer, reviewed_at, review_notes, "
    "resolution_status, fulfillment_assertion_id, quality_score, "
    "prospective_summary, events_json, artifact_uri, artifact_storage, "
    "entrenchment_score, created_at"
)

_VALID_REVIEW_STATUS = {"committed", "flagged", "staged", "rejected"}


@router.get("", response_model=AssertionList)
def list_assertions(
    entity_id: str | None = None,
    confidence: str | None = None,
    review_status: str | None = None,
    superseded: bool | None = None,
    entity_type: str | None = Query(
        None, description="Filter to assertions on entities of this type"
    ),
    entity_type_exclude: str | None = Query(
        None,
        description="Comma-separated entity types to exclude (e.g. 'legal_matter,person')",
    ),
    valid_at: str | None = Query(
        None, description="World-state: what was true at this date (YYYY-MM-DD)"
    ),
    known_at: str | None = Query(
        None, description="System-state: what the DB knew at this date (YYYY-MM-DD)"
    ),
    limit: int = Query(50, ge=1, le=500),
) -> AssertionList:
    """List assertions with entity, confidence, review_status, superseded, entity type, and temporal filters."""
    clauses: list[str] = []
    params: list[str | int] = []
    needs_join = bool(entity_type or entity_type_exclude)

    if entity_id:
        clauses.append("a.entity_id = ?")
        params.append(entity_id)
    if confidence:
        clauses.append("a.confidence = ?")
        params.append(confidence)
    if review_status:
        clauses.append("a.review_status = ?")
        params.append(review_status)
    if superseded is False:
        clauses.append("a.superseded_by IS NULL")
    elif superseded is True:
        clauses.append("a.superseded_by IS NOT NULL")

    if entity_type:
        clauses.append("e.type = ?")
        params.append(entity_type)
    if entity_type_exclude:
        excluded = [t.strip() for t in entity_type_exclude.split(",") if t.strip()]
        placeholders = ",".join("?" for _ in excluded)
        clauses.append(f"e.type NOT IN ({placeholders})")
        params.extend(excluded)

    if valid_at:
        clauses.append("(a.valid_from IS NULL OR a.valid_from <= ?)")
        params.append(valid_at)
        clauses.append("(a.valid_until IS NULL OR a.valid_until > ?)")
        params.append(valid_at)
        clauses.append("a.superseded_by IS NULL")
    elif known_at:
        clauses.append("a.created_at <= ?")
        params.append(known_at)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    if needs_join:
        cols = ", ".join(f"a.{c.strip()}" for c in _ASSERTION_COLS.split(","))
        sql = (
            f"SELECT {cols} FROM assertions a "
            f"JOIN entities e ON a.entity_id = e.id{where} "
            f"ORDER BY a.created_at DESC LIMIT ?"
        )
    else:
        cols = _ASSERTION_COLS
        sql = f"SELECT {cols} FROM assertions a{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    items: list[AssertionItem] = []
    for row in rows:
        try:
            items.append(AssertionItem(**decode_row(row, _JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s — deserialization failed",
                row.get("id"),
                exc_info=True,
            )
    hints = detect_expired_unresolved([i.model_dump() for i in items])
    return AssertionList(items=items, action_hints=hints or None)


_SEARCH_COLS = (
    "a.id, a.entity_id, a.claim, a.confidence, a.confidence_score, "
    "a.evidence, a.evidence_uris, a.seeded_by, a.derivation_type, "
    "a.prospective_summary, a.events_json, a.superseded_by, "
    "a.entrenchment_score, a.observed_at, a.created_at"
)


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


_SEARCH_COLS_WITH_ENTITY = _SEARCH_COLS + ", e.name AS entity_name"


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
) -> AssertionSearchResult:
    """Hybrid search: FTS5 + vector similarity with CombMAX score fusion.

    Falls back to FTS5-only when the embedding model is unavailable.
    """
    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return AssertionSearchResult(query=q, items=[], total=0, search_mode="fulltext")

    with cortex_conn() as conn:
        fts_rows = _fts_search(conn, sanitized, superseded, entity_type, limit * 2)

    vector_results = _vector_search(q, n_results=limit * 2)

    fused, search_mode = _combmax_fuse(fts_rows, vector_results, limit)

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


@router.post("", response_model=AssertionCreateResponse)
def create_assertion(
    body: AssertionCreate, response: Response
) -> AssertionCreateResponse:
    """Create an assertion with quality validation and idempotent dedup.

    v2.4 enforcement: hard rejects return 422 with specific diagnostics.
    Warnings route the assertion to staging (review_status='staged').
    Quality score is computed and stored on every new assertion.
    """
    if body.confidence not in _VALID_CONFIDENCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid confidence: {body.confidence!r}. Must be one of {sorted(_VALID_CONFIDENCE)}",
        )

    validation = validate_assertion(body)

    if validation.rejected:
        diagnostics = [
            {"field": d.field, "message": d.message} for d in validation.hard_reject
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "assertion_quality_rejected",
                "quality_score": validation.quality_score,
                "diagnostics": diagnostics,
            },
        )

    review_status: str | None = None
    validation_warnings: list[dict[str, str]] | None = None
    if validation.route_to_staging:
        review_status = "staged"
        validation_warnings = [
            {"field": d.field, "message": d.message} for d in validation.warnings
        ]
        logger.info(
            "Assertion routed to staging (quality_score=%.2f): %s",
            validation.quality_score,
            body.entity_id,
        )

    claim_hash = compute_claim_hash(body.entity_id, body.claim)

    conn = cortex_conn()
    try:
        entities = query(
            conn, "SELECT id FROM entities WHERE id = ?", (body.entity_id,)
        )
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {body.entity_id}",
            )

        # C2: Write-path contradiction check (entity-local, AGM G3)
        contradiction_warnings_out: list[ContradictionConflict] | None = None
        if body.force and body.supersedes_id is not None:
            sup_target = query(
                conn,
                "SELECT id FROM assertions WHERE id = ?",
                (body.supersedes_id,),
            )
            if not sup_target:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(f"supersedes_id assertion not found: {body.supersedes_id}"),
                )

        guard = guard_assertion_write(
            conn, body.entity_id, body.claim, force=body.force
        )
        if not guard.allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=guard.block_detail,
            )
        if guard.review_status:
            review_status = guard.review_status
        if guard.contradiction_warnings:
            contradiction_warnings_out = [
                ContradictionConflict(
                    assertion_id=c.assertion_id,
                    claim=c.claim,
                    confidence=c.confidence,
                    similarity=c.similarity,
                )
                for c in guard.contradiction_warnings
            ]

        entrenchment = compute_entrenchment(
            confidence=body.confidence,
            derivation_type=body.derivation_type or "inference",
            observed_at=body.observed_at,
            created_at=None,
            entity_id=body.entity_id,
            conn=conn,
        )

        near_dup_warning: NearDuplicateWarning | None = None

        with WRITE_LOCK:
            cur = conn.execute(
                "INSERT OR IGNORE INTO assertions ("
                "  entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by,"
                "  chunk_id, derivation_type, reasoning_summary, observed_at,"
                "  valid_from, valid_until, is_atomic, is_decontextualized, claim_hash,"
                "  resolution_status, fulfillment_assertion_id, quality_score, review_status,"
                "  prospective_summary, events_json, artifact_uri, artifact_storage,"
                "  entrenchment_score"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    body.entity_id,
                    body.claim,
                    body.confidence,
                    body.confidence_score,
                    body.evidence,
                    json_encode(body.evidence_uris),
                    body.seeded_by,
                    body.chunk_id,
                    body.derivation_type or "inference",
                    body.reasoning_summary,
                    body.observed_at,
                    body.valid_from,
                    body.valid_until,
                    body.is_atomic,
                    body.is_decontextualized,
                    claim_hash,
                    body.resolution_status,
                    body.fulfillment_assertion_id,
                    validation.quality_score,
                    review_status,
                    body.prospective_summary,
                    body.events_json,
                    body.artifact_uri,
                    body.artifact_storage,
                    entrenchment,
                ),
            )

            was_new = cur.rowcount > 0
            new_id = cur.lastrowid

            if was_new:
                if body.force and body.supersedes_id:
                    import datetime as dt

                    now_str = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    conn.execute(
                        "UPDATE assertions SET superseded_by = ?, valid_until = ?, "
                        "updated_at = ? WHERE id = ? AND superseded_by IS NULL",
                        (new_id, now_str, now_str, body.supersedes_id),
                    )

                if contradiction_warnings_out:
                    c2_notes = "; ".join(
                        f"Semantic contradiction: #{c.assertion_id} "
                        f"(sim={c.similarity:.2f})"
                        for c in contradiction_warnings_out
                    )
                    conn.execute(
                        "UPDATE assertions SET review_notes = ? WHERE id = ?",
                        (c2_notes, new_id),
                    )

                match = check_near_duplicate(conn, body.entity_id, body.claim, new_id)
                if match:
                    record_near_duplicate(conn, new_id, match.existing_id, match.score)
                    near_dup_warning = NearDuplicateWarning(
                        existing_id=match.existing_id, score=match.score
                    )

                contradiction = check_contradictions(conn, body.entity_id, body.claim)
                if contradiction.flagged:
                    conn.execute(
                        "UPDATE assertions SET review_status = ?, "
                        "review_notes = CASE WHEN review_notes IS NOT NULL "
                        "THEN review_notes || '; ' || ? ELSE ? END "
                        "WHERE id = ?",
                        (
                            "flagged",
                            contradiction.review_notes,
                            contradiction.review_notes,
                            new_id,
                        ),
                    )
                    logger.info(
                        "Assertion %d flagged: contradiction with %s via edge #%s",
                        new_id,
                        contradiction.contradicting_entity,
                        contradiction.edge_id,
                    )

            conn.commit()

        if was_new:
            rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
                (new_id,),
            )
        else:
            rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions "
                "WHERE entity_id = ? AND claim_hash = ? AND superseded_by IS NULL",
                (body.entity_id, claim_hash),
            )
    finally:
        conn.close()

    if not rows:
        logger.error(
            "Assertion create: no row found for entity_id=%s claim_hash=%s was_new=%s",
            body.entity_id,
            claim_hash[:16],
            was_new,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assertion created but could not be read back",
        )

    item = AssertionItem(**decode_row(rows[0], _JSON_FIELDS))
    response.status_code = status.HTTP_201_CREATED if was_new else status.HTTP_200_OK
    if not was_new:
        logger.info(
            "Assertion dedup: exact duplicate for entity_id=%s, returning existing id=%d",
            body.entity_id,
            item.id,
        )
    else:
        threading.Thread(
            target=reindex_assertion_fts, args=(item.id,), daemon=True
        ).start()
        enrich_background(item.id, body.claim, body.entity_id, body.confidence)
        _embed_assertion_background(
            item.id,
            {
                "claim": body.claim,
                "entity_id": body.entity_id,
                "confidence": body.confidence,
                "derivation_type": body.derivation_type or "inference",
                "entrenchment_score": entrenchment,
                "observed_at": body.observed_at,
                "prospective_summary": body.prospective_summary,
                "events_json": body.events_json,
            },
        )

    return AssertionCreateResponse(
        was_new=was_new,
        item=item,
        near_duplicate_warning=near_dup_warning,
        validation_warnings=validation_warnings,
        contradiction_warnings=contradiction_warnings_out,
    )


@router.patch("/{assertion_id}", response_model=AssertionItem)
def update_assertion(assertion_id: int, body: AssertionUpdate) -> AssertionItem:
    """Update assertion metadata — supersession, confidence, review status."""
    import datetime as dt

    with cortex_conn() as conn:
        existing = query(
            conn, "SELECT id FROM assertions WHERE id = ?", (assertion_id,)
        )
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Assertion not found: {assertion_id}",
            )

        if body.superseded_by is not None:
            target = query(
                conn, "SELECT id FROM assertions WHERE id = ?", (body.superseded_by,)
            )
            if not target:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Superseding assertion not found: {body.superseded_by}",
                )

        if (
            body.review_status is not None
            and body.review_status not in _VALID_REVIEW_STATUS
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid review_status: {body.review_status!r}. "
                f"Must be one of {sorted(_VALID_REVIEW_STATUS)}",
            )

        if body.confidence is not None and body.confidence not in _VALID_CONFIDENCE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid confidence: {body.confidence!r}. "
                f"Must be one of {sorted(_VALID_CONFIDENCE)}",
            )

        update_map: dict[str, object] = {
            "superseded_by": body.superseded_by,
            "valid_until": body.valid_until,
            "confidence": body.confidence,
            "confidence_score": body.confidence_score,
            "review_status": body.review_status,
            "reviewer": body.reviewer,
            "reviewed_at": body.reviewed_at,
            "resolution_status": body.resolution_status,
            "fulfillment_assertion_id": body.fulfillment_assertion_id,
        }
        sets: list[str] = []
        params: list[object] = []
        for col, val in update_map.items():
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)

        if not sets:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No updatable fields provided",
            )

        now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        sets.append("updated_at = ?")
        params.append(now)
        params.append(assertion_id)

        with WRITE_LOCK:
            conn.execute(
                f"UPDATE assertions SET {', '.join(sets)} WHERE id = ?", tuple(params)
            )
            conn.commit()

        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )

    return AssertionItem(**decode_row(rows[0], _JSON_FIELDS))


@router.post(
    "/supersede", response_model=SupersedeResponse, status_code=status.HTTP_201_CREATED
)
def supersede_assertion(body: SupersedeRequest) -> SupersedeResponse:
    """Atomic supersession — close old assertion and create replacement in one transaction."""
    import datetime as dt

    if body.confidence not in _VALID_CONFIDENCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid confidence: {body.confidence!r}. Must be one of {sorted(_VALID_CONFIDENCE)}",
        )

    now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = cortex_conn()
    try:
        old_rows = query(
            conn, "SELECT id FROM assertions WHERE id = ?", (body.old_assertion_id,)
        )
        if not old_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Old assertion not found: {body.old_assertion_id}",
            )

        entities = query(
            conn, "SELECT id FROM entities WHERE id = ?", (body.entity_id,)
        )
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {body.entity_id}",
            )

        # C1: Validate supersession target against semantic impact
        impact = analyze_assertion_impact(
            conn, body.entity_id, body.claim, body.confidence
        )
        touched_ids = {t.assertion_id for t in impact.touched_assertions}
        impact_warning: str | None = None
        if (
            body.old_assertion_id not in impact.likely_supersedes
            and body.old_assertion_id not in touched_ids
        ):
            impact_warning = (
                f"Assertion {body.old_assertion_id} not found in semantic "
                f"impact analysis — target may not be the most relevant match"
            )
            logger.warning(
                "Supersede target %d has low semantic relevance to new claim",
                body.old_assertion_id,
            )

        entrenchment = compute_entrenchment(
            confidence=body.confidence,
            derivation_type=body.derivation_type or "inference",
            observed_at=now,
            created_at=None,
            entity_id=body.entity_id,
            conn=conn,
        )

        with WRITE_LOCK:
            cur = conn.execute(
                "INSERT INTO assertions ("
                "  entity_id, claim, confidence, evidence, evidence_uris,"
                "  derivation_type, observed_at, valid_from, entrenchment_score"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    body.entity_id,
                    body.claim,
                    body.confidence,
                    body.evidence,
                    json_encode(body.evidence_uris),
                    body.derivation_type or "inference",
                    now,
                    body.valid_from,
                    entrenchment,
                ),
            )
            new_id = cur.lastrowid

            conn.execute(
                "UPDATE assertions SET valid_until = ?, superseded_by = ?, updated_at = ? "
                "WHERE id = ?",
                (now, new_id, now, body.old_assertion_id),
            )
            conn.execute(
                "INSERT INTO session_edges ("
                "  session_id, agent, from_node, to_node, edge_type, strength, edge_source, context"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    body.session_id,
                    body.agent,
                    f"assertion:{new_id}",
                    f"assertion:{body.old_assertion_id}",
                    "supersedes",
                    1.0,
                    "derived",
                    "auto-created by supersede tool",
                ),
            )
            conn.commit()

        enrich_old_assertion_events(conn, body.old_assertion_id)

        old_result = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (body.old_assertion_id,),
        )
        new_result = query(
            conn, f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?", (new_id,)
        )
    finally:
        conn.close()

    if not old_result or not new_result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supersession committed but could not read back results",
        )

    threading.Thread(
        target=reindex_assertion_fts, args=(new_id,), daemon=True
    ).start()
    enrich_background(new_id, body.claim, body.entity_id, body.confidence)

    vector_store.delete_assertion_embedding(body.old_assertion_id)
    _embed_assertion_background(
        new_id,
        {
            "claim": body.claim,
            "entity_id": body.entity_id,
            "confidence": body.confidence,
            "derivation_type": body.derivation_type or "inference",
            "entrenchment_score": entrenchment,
            "observed_at": now,
            "prospective_summary": None,
            "events_json": None,
        },
    )

    return SupersedeResponse(
        old=AssertionItem(**decode_row(old_result[0], _JSON_FIELDS)),
        new=AssertionItem(**decode_row(new_result[0], _JSON_FIELDS)),
        impact_warning=impact_warning,
    )


@router.get("/entrenchment", response_model=AssertionList)
def list_assertions_by_entrenchment(
    entity_id: str = Query(..., description="Entity to list assertions for"),
    superseded: bool = Query(False, description="Include superseded assertions"),
    limit: int = Query(50, ge=1, le=500),
) -> AssertionList:
    """List assertions ordered by entrenchment score (descending).

    Returns the belief base for an entity ordered by resistance to contraction.
    K÷7 (Superexpansion): lower-entrenchment beliefs contract first.
    """
    clauses: list[str] = ["entity_id = ?"]
    params: list[str | int] = [entity_id]

    if not superseded:
        clauses.append("superseded_by IS NULL")

    where = " AND ".join(clauses)
    sql = (
        f"SELECT {_ASSERTION_COLS} FROM assertions "
        f"WHERE {where} "
        "ORDER BY COALESCE(entrenchment_score, 0.0) DESC LIMIT ?"
    )
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    items: list[AssertionItem] = []
    for row in rows:
        try:
            items.append(AssertionItem(**decode_row(row, _JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s — deserialization failed",
                row.get("id"),
                exc_info=True,
            )
    return AssertionList(items=items)


@router.post("/{assertion_id}/enrich", response_model=EnrichResponse)
def enrich_assertion_endpoint(
    assertion_id: int, body: EnrichRequest | None = None
) -> EnrichResponse:
    """Explicitly trigger enrichment on an existing assertion.

    Accepts an optional list of enrichment kinds (``prospective``, ``events``).
    Defaults to all available enrichments. Runs synchronously and updates the
    assertion row before returning.
    """
    with cortex_conn() as conn:
        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assertion not found: {assertion_id}",
        )

    row = decode_row(rows[0], _JSON_FIELDS)
    kinds = {"prospective", "events"}
    if body and body.enrichments:
        kinds = set(body.enrichments)

    results = enrich_assertion(
        assertion_id,
        row["claim"],
        row["entity_id"],
        row["confidence"],
        kinds=kinds,
    )

    with cortex_conn() as conn:
        updated_rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )

    item = AssertionItem(**decode_row(updated_rows[0], _JSON_FIELDS))
    return EnrichResponse(
        item=item,
        enrichments_run=sorted(kinds),
        results=results,
    )
