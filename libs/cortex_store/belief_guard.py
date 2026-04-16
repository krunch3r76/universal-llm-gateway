"""Belief guard — C1 semantic impact analysis + C2 write-path contradiction check.

Enforces entity-local belief consistency (AGM G3) on the assertion write path.
C2 runs before INSERT: conflicts with confirmed assertions block (HTTP 409),
conflicts with believed assertions proceed with review_status='flagged'.
force=True + supersedes_id bypasses C2 for explicit, auditable revision.

Exempt callers (bypass audit — Phase C):
- POST /assertions/supersede — explicit supersession path, has C1 validation instead
- POST /assert-from-chunk (ingest.py) — extraction-derived, bulk operation,
  polarity check not meaningful for chunk-to-assertion extraction
- POST /staging/.../approve (staging.py) — human-approved proposals, analogous to
  force=True; human review replaces automated contradiction check
- rag/fts_index.py — only writes to chunks_fts, not assertions table (confirmed)
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from . import embeddings as cortex_embeddings
from . import vector_store
from .db import query as db_query
from .polarity import build_candidate_query, detect_polarity_conflict

logger = logging.getLogger("cortex-api.belief-guard")

TOUCHED_COSINE_THRESHOLD = 0.72
SUPERSEDE_COSINE_THRESHOLD = 0.85
CONTRADICTION_SIMILARITY_THRESHOLD = 0.80

_CONFIDENCE_RANK = {"confirmed": 3, "believed": 2, "suspected": 1, "hypothesized": 0}


# ── Entity-Scoped Hybrid Search ──────────────────────────────────────────


@dataclass
class SimilarAssertion:
    assertion_id: int
    claim: str
    confidence: str
    similarity: float
    entity_id: str
    retrieval_source: str


def _entity_fts_search(
    conn: sqlite3.Connection,
    claim_text: str,
    entity_id: str,
    limit: int = 20,
) -> list[dict]:
    """FTS5 search scoped to a single entity's active assertions."""
    fts_query = build_candidate_query(claim_text)
    if not fts_query:
        return []
    try:
        return db_query(
            conn,
            "SELECT a.id, a.claim, a.confidence, a.entity_id, rank "
            "FROM assertions_fts f "
            "JOIN assertions a ON a.id = f.assertion_id "
            "WHERE f.indexed_text MATCH ? AND a.entity_id = ? "
            "AND a.superseded_by IS NULL "
            "ORDER BY rank LIMIT ?",
            (fts_query, entity_id, limit),
        )
    except Exception:
        logger.warning("Entity FTS search failed", exc_info=True)
        return []


def _entity_vector_search(
    claim_text: str,
    entity_id: str,
    n_results: int = 20,
) -> list[dict]:
    """Vector search scoped to a single entity via ChromaDB metadata filter."""
    if not cortex_embeddings.is_configured() or not vector_store.is_initialized():
        return []
    try:
        embedding = cortex_embeddings.embed_query(claim_text)
        return vector_store.search_by_entity(embedding, entity_id, n_results)
    except Exception:
        logger.warning("Entity vector search failed — FTS only", exc_info=True)
        return []


def _entity_hybrid_search(
    conn: sqlite3.Connection,
    claim_text: str,
    entity_id: str,
    limit: int = 20,
) -> list[SimilarAssertion]:
    """Hybrid FTS5 + vector search for similar assertions on the same entity."""
    fts_rows = _entity_fts_search(conn, claim_text, entity_id, limit * 2)
    # ∀ new entity: no FTS candidates ⟹ no active assertions ⟹ no contradiction possible.
    # reindex_assertion_fts is always called at assertion creation, so empty FTS means
    # the entity has no indexed assertions. Skip the embedding HTTP call entirely to
    # avoid blocking the write path on a Stargate round-trip that yields nothing.
    if not fts_rows:
        return []
    vector_results = _entity_vector_search(claim_text, entity_id, limit * 2)

    max_rank = max((abs(r.get("rank", 0)) for r in fts_rows), default=1.0) or 1.0
    merged: dict[int, dict] = {}

    for row in fts_rows:
        aid = row["id"]
        bm25 = abs(row.get("rank", 0)) / max_rank
        merged[aid] = {
            "assertion_id": aid,
            "claim": row.get("claim", ""),
            "confidence": row.get("confidence", ""),
            "entity_id": entity_id,
            "bm25": bm25,
            "cosine": None,
            "similarity": bm25,
            "source": "fts",
        }

    for vr in vector_results:
        aid = vr["assertion_id"]
        cosine = vr.get("cosine_similarity", 0.0)
        if aid in merged:
            m = merged[aid]
            m["cosine"] = cosine
            m["similarity"] = max(m["bm25"], cosine)
            m["source"] = "both"
        else:
            merged[aid] = {
                "assertion_id": aid,
                "claim": "",
                "confidence": "",
                "entity_id": entity_id,
                "bm25": None,
                "cosine": cosine,
                "similarity": cosine,
                "source": "vector",
            }

    vo_ids = [m["assertion_id"] for m in merged.values() if m["source"] == "vector"]
    if vo_ids:
        ph = ",".join("?" for _ in vo_ids)
        rows = db_query(
            conn,
            f"SELECT id, claim, confidence FROM assertions WHERE id IN ({ph})",
            tuple(vo_ids),
        )
        by_id = {r["id"]: r for r in rows}
        for aid in vo_ids:
            if aid in by_id:
                merged[aid]["claim"] = by_id[aid]["claim"]
                merged[aid]["confidence"] = by_id[aid]["confidence"]

    sorted_items = sorted(
        merged.values(), key=lambda x: x.get("similarity", 0.0), reverse=True
    )
    return [
        SimilarAssertion(
            assertion_id=item["assertion_id"],
            claim=item["claim"],
            confidence=item["confidence"],
            similarity=round(item["similarity"], 4),
            entity_id=item["entity_id"],
            retrieval_source=item["source"],
        )
        for item in sorted_items[:limit]
    ]


# ── C1: Semantic Impact Analysis ─────────────────────────────────────────


@dataclass
class ImpactAnalysis:
    touched_assertions: list[SimilarAssertion] = field(default_factory=list)
    likely_supersedes: list[int] = field(default_factory=list)
    implicated_entities: list[str] = field(default_factory=list)
    impact_score: float = 0.0


def analyze_assertion_impact(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
    confidence: str,
) -> ImpactAnalysis:
    """Compute semantic impact of a proposed assertion before commit.

    Uses entity-scoped hybrid search to find existing assertions affected
    by the new claim. Returns touched assertions, likely supersession
    targets, implicated entities, and an impact score (0-1).
    """
    from .graph_utils import extract_entity_ids

    similar = _entity_hybrid_search(conn, claim, entity_id, limit=20)
    touched = [s for s in similar if s.similarity >= TOUCHED_COSINE_THRESHOLD]

    conf_rank = _CONFIDENCE_RANK.get(confidence, 0)
    likely_supersedes = [
        s.assertion_id
        for s in similar
        if s.similarity >= SUPERSEDE_COSINE_THRESHOLD
        and _CONFIDENCE_RANK.get(s.confidence, 0) <= conf_rank
    ]

    mentioned = extract_entity_ids(claim)
    mentioned.discard(entity_id)

    return ImpactAnalysis(
        touched_assertions=touched,
        likely_supersedes=likely_supersedes,
        implicated_entities=sorted(mentioned),
        impact_score=round(min(1.0, len(touched) * 0.1), 2),
    )


# ── C2: Write-Path Contradiction Check ───────────────────────────────────


@dataclass
class ConflictDetail:
    assertion_id: int
    claim: str
    confidence: str
    similarity: float


@dataclass
class ContradictionResult:
    conflicts: list[ConflictDetail] = field(default_factory=list)
    safe: bool = True


def check_write_contradiction(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
) -> ContradictionResult:
    """Detect entity-local semantic contradictions before assertion commit.

    Uses FTS5 only — no embedding call. The write path must not block on a
    model HTTP round-trip; vector search improves recall but runs post-commit.
    Scoped to entity-local only (AGM G3, not global consistency).
    """
    fts_rows = _entity_fts_search(conn, claim, entity_id, limit=10)
    similar = [
        SimilarAssertion(
            assertion_id=r["id"],
            claim=r["claim"],
            confidence=r["confidence"],
            similarity=0.0,
            entity_id=entity_id,
            retrieval_source="fts",
        )
        for r in fts_rows
    ]

    # FTS already filtered for textual relevance; check polarity on all candidates.
    # Cosine-based threshold is not applicable to BM25 ranks — omit it here.
    conflicts: list[ConflictDetail] = []
    for s in similar:
        if not detect_polarity_conflict(claim, s.claim):
            continue
        conflicts.append(
            ConflictDetail(
                assertion_id=s.assertion_id,
                claim=s.claim,
                confidence=s.confidence,
                similarity=s.similarity,
            )
        )

    return ContradictionResult(conflicts=conflicts, safe=not conflicts)


# ── Write Guard (facade for route handlers) ──────────────────────────────


@dataclass
class WriteGuardResult:
    """Result of C2 write-path contradiction check."""

    allowed: bool = True
    review_status: str | None = None
    contradiction_warnings: list[ConflictDetail] | None = None
    block_detail: dict | None = None


def guard_assertion_write(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
    *,
    force: bool = False,
) -> WriteGuardResult:
    """Run C2 contradiction check. Returns immediately if force=True."""
    if force:
        return WriteGuardResult(allowed=True)

    result = check_write_contradiction(conn, entity_id, claim)
    if result.safe:
        return WriteGuardResult(allowed=True)

    confirmed = [c for c in result.conflicts if c.confidence == "confirmed"]
    if confirmed:
        return WriteGuardResult(
            allowed=False,
            review_status="flagged",
            contradiction_warnings=result.conflicts,
            block_detail={
                "error": "contradiction_detected",
                "message": (
                    f"Contradicts {len(confirmed)} confirmed assertion(s) "
                    f"on {entity_id}"
                ),
                "conflicts": [
                    {
                        "assertion_id": c.assertion_id,
                        "claim": c.claim,
                        "confidence": c.confidence,
                        "similarity": c.similarity,
                    }
                    for c in confirmed
                ],
            },
        )

    return WriteGuardResult(
        allowed=True,
        review_status="flagged",
        contradiction_warnings=result.conflicts,
    )
