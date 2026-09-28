"""Belief guard — C1 semantic impact analysis + C2 write-path contradiction check.

Enforces entity-local belief consistency (AGM G3) on the assertion write path.
C2 runs before INSERT. A 409 block requires embedding cosine at or above
``CONTRADICTION_SIMILARITY_THRESHOLD`` (0.80), confidence ``confirmed``, and
``detect_polarity_conflict``. An FTS rank ratio is not a similarity and never
blocks. When embeddings are unavailable, polarity hits are flagged
(``retrieval_source=fts``, log line ``c2_degraded=lexical``) and the write
proceeds. The assert nudge and ``guard_assertion_write`` share one pre-INSERT
hybrid recall so MCP assert does not search twice. ``force=True`` bypasses C2.

Exempt callers (bypass audit — Phase C):
- POST /assertions/supersede — explicit supersession path, has C1 validation instead
- POST /assert-from-chunk (ingest.py) — extraction-derived, bulk operation,
  polarity check not meaningful for chunk-to-assertion extraction
- POST /staging/.../approve (staging.py) — human-approved proposals, analogous to
  force=True; human review replaces automated contradiction check
- rag/fts_index.py — only writes to chunks_fts, not assertions table (confirmed)
"""

from __future__ import annotations

import contextvars
import sqlite3
from dataclasses import dataclass, field

from universal_logging import get_logger

from . import embeddings as cortex_embeddings
from . import vector_store
from .db import query as db_query
from .polarity import build_candidate_query, detect_polarity_conflict

logger = get_logger("cortex-api.belief-guard")

# Gate embedding cosine (claim vs candidate), not an FTS rank ratio.
# abs(rank)/max(abs(rank)) over the FTS result is identically 1.0 for the
# top row whenever FTS returns anything, so those thresholds cannot bind on it.
TOUCHED_COSINE_THRESHOLD = 0.72
SUPERSEDE_COSINE_THRESHOLD = 0.85
CONTRADICTION_SIMILARITY_THRESHOLD = 0.80

_CONFIDENCE_RANK = {"confirmed": 3, "believed": 2, "suspected": 1, "hypothesized": 0}

# Event-log anchor entities (conversation turn records, id scheme
# ``thread:{kind}:{key}`` — see thread_persistence/binding.py) are append-only.
# Their assertions are immutable turn events, not belief claims, so they sit
# categorically outside AGM entity-local consistency (C2). Running C2 on them
# hard-blocks a reused dispatch_thread_id replay the moment two turns share a
# status-antonym pair (agent-bus:1195).
_EVENT_LOG_ANCHOR_PREFIXES = ("thread:",)


def is_event_log_entity(entity_id: str) -> bool:
    """True iff ``entity_id`` is an append-only conversation-log anchor.

    ``thread:*`` ids store immutable turn records, so the C2 cosine gate
    must not treat them as belief claims.
    """
    return entity_id.startswith(_EVENT_LOG_ANCHOR_PREFIXES)


def predicate_functor(predicate_form: str | None) -> str | None:
    """Return the predicate head (text before first ``(``), or None when absent."""
    if not predicate_form:
        return None
    head, _sep, _rest = predicate_form.partition("(")
    head = head.strip()
    return head or None


# ── Entity-Scoped Hybrid Search ──────────────────────────────────────────


@dataclass
class SimilarAssertion:
    assertion_id: int
    claim: str
    confidence: str
    similarity: float
    entity_id: str
    retrieval_source: str
    predicate_form: str | None = None


@dataclass
class _LexicalHit:
    assertion_id: int
    claim: str
    confidence: str


@dataclass
class CandidateRecall:
    """One pre-INSERT hybrid recall shared by the assert nudge and C2.

    ``scored`` rows carry embedding cosine only. ``lexical_rows`` is filled
    only when embeddings cannot score, and those rows have no similarity
    field, so an FTS rank ratio cannot be stored under that name.
    """

    scored: list[SimilarAssertion]
    embeddings_unavailable: bool
    lexical_rows: list[_LexicalHit] = field(default_factory=list)


# Set by ``_entity_vector_search`` for the in-flight recall. A patched search
# leaves the value the caller stored (False), so tests that inject cosine are
# not treated as the lexical-degraded path.
_vector_unavailable: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "c2_vector_unavailable", default=False
)
_last_recall: contextvars.ContextVar[CandidateRecall | None] = contextvars.ContextVar(
    "c2_last_recall", default=None
)
_staged_recall: contextvars.ContextVar[tuple[str, str, CandidateRecall] | None] = (
    contextvars.ContextVar("c2_staged_recall", default=None)
)


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
            "SELECT a.id, a.claim, a.confidence, a.entity_id, a.predicate_form, rank "
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
    """Vector search scoped to a single entity via ChromaDB metadata filter.

    Sets ``_vector_unavailable`` when embeddings are unconfigured or the
    search raises, so C2 can flag lexical evidence instead of blocking.
    """
    if not cortex_embeddings.is_configured() or not vector_store.is_initialized():
        _vector_unavailable.set(True)
        return []
    try:
        embedding = cortex_embeddings.embed_query(claim_text)
        hits = vector_store.search_by_entity(embedding, entity_id, n_results)
    except Exception:
        logger.warning("Entity vector search failed — FTS only", exc_info=True)
        _vector_unavailable.set(True)
        return []
    _vector_unavailable.set(False)
    return hits


def _assemble_hybrid(
    conn: sqlite3.Connection,
    claim_text: str,
    entity_id: str,
    limit: int = 20,
) -> CandidateRecall:
    """FTS recall plus cosine scores, with a lexical fallback when embeddings are down.

    Empty FTS skips the embedding call: a new entity has no indexed assertions,
    so a Stargate round-trip cannot find a contradiction. Similarity on scored
    rows is embedding cosine only. Unscored rows are omitted, not filled with
    ``abs(rank)/max(rank)``.
    """
    fts_rows = _entity_fts_search(conn, claim_text, entity_id, limit * 2)
    if not fts_rows:
        return CandidateRecall(scored=[], embeddings_unavailable=False)
    _vector_unavailable.set(False)
    vector_results = _entity_vector_search(claim_text, entity_id, limit * 2)
    unavailable = _vector_unavailable.get()

    merged: dict[int, dict] = {}

    for row in fts_rows:
        aid = row["id"]
        merged[aid] = {
            "assertion_id": aid,
            "claim": row.get("claim", ""),
            "confidence": row.get("confidence", ""),
            "entity_id": entity_id,
            "predicate_form": row.get("predicate_form"),
            "cosine": None,
            "source": "fts",
        }

    for vr in vector_results:
        aid = vr["assertion_id"]
        cosine = float(vr.get("cosine_similarity", 0.0))
        if aid in merged:
            m = merged[aid]
            m["cosine"] = cosine
            m["source"] = "both"
        else:
            merged[aid] = {
                "assertion_id": aid,
                "claim": "",
                "confidence": "",
                "entity_id": entity_id,
                "predicate_form": None,
                "cosine": cosine,
                "source": "vector",
            }

    vo_ids = [m["assertion_id"] for m in merged.values() if m["source"] == "vector"]
    if vo_ids:
        ph = ",".join("?" for _ in vo_ids)
        rows = db_query(
            conn,
            f"SELECT id, claim, confidence, predicate_form FROM assertions WHERE id IN ({ph})",
            tuple(vo_ids),
        )
        by_id = {r["id"]: r for r in rows}
        for aid in vo_ids:
            if aid in by_id:
                merged[aid]["claim"] = by_id[aid]["claim"]
                merged[aid]["confidence"] = by_id[aid]["confidence"]
                merged[aid]["predicate_form"] = by_id[aid].get("predicate_form")

    scored = [m for m in merged.values() if m["cosine"] is not None]
    sorted_items = sorted(scored, key=lambda x: x["cosine"], reverse=True)
    scored_rows = [
        SimilarAssertion(
            assertion_id=item["assertion_id"],
            claim=item["claim"],
            confidence=item["confidence"],
            similarity=round(item["cosine"], 4),
            entity_id=item["entity_id"],
            retrieval_source=item["source"],
            predicate_form=item.get("predicate_form"),
        )
        for item in sorted_items[:limit]
    ]
    lexical: list[_LexicalHit] = []
    if unavailable:
        lexical = [
            _LexicalHit(
                assertion_id=row["id"],
                claim=row.get("claim", ""),
                confidence=row.get("confidence", ""),
            )
            for row in fts_rows
        ]
    return CandidateRecall(
        scored=scored_rows,
        embeddings_unavailable=unavailable,
        lexical_rows=lexical,
    )


def _entity_hybrid_search(
    conn: sqlite3.Connection,
    claim_text: str,
    entity_id: str,
    limit: int = 20,
) -> list[SimilarAssertion]:
    """Hybrid FTS5 recall plus embedding-cosine similarity on one entity.

    FTS (``build_candidate_query``) only recalls candidates. Similarity is the
    embedding cosine of this claim against that assertion. A within-set BM25
    ratio is not a similarity: the top FTS row is identically 1.0 whenever any
    row comes back, so ``TOUCHED_COSINE_THRESHOLD`` and
    ``SUPERSEDE_COSINE_THRESHOLD`` would not bind. Rows the vector search did
    not score are omitted rather than reported as that ratio. The full recall,
    including the embeddings-unavailable flag, is stashed for C2 so the assert
    nudge and the guard share this single search.
    """
    recall = _assemble_hybrid(conn, claim_text, entity_id, limit)
    _last_recall.set(recall)
    return recall.scored


def stage_candidate_recall(entity_id: str, claim: str, recall: CandidateRecall) -> None:
    """Park one hybrid recall for the next C2 guard on this entity and claim.

    The assert nudge calls this via ``analyze_assertion_impact``. The guard
    consumes the park so MCP assert does not run a second hybrid search.
    """
    _staged_recall.set((entity_id, claim, recall))


def take_staged_candidate_recall(entity_id: str, claim: str) -> CandidateRecall | None:
    """Return the parked recall when entity and claim match, else leave it.

    A mismatch is not consumed: a different write in the same context must not
    steal the nudge's search result. A match is cleared so it cannot leak.
    """
    staged = _staged_recall.get()
    if staged is None:
        return None
    staged_entity, staged_claim, recall = staged
    if staged_entity != entity_id or staged_claim != claim:
        return None
    _staged_recall.set(None)
    return recall


def clear_staged_candidate_recall() -> None:
    """Drop a parked recall after the create path finishes or aborts early."""
    _staged_recall.set(None)


def _take_last_recall() -> CandidateRecall | None:
    recall = _last_recall.get()
    _last_recall.set(None)
    return recall


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
    predicate_form: str | None = None,
) -> ImpactAnalysis:
    """Compute semantic impact of a proposed assertion before commit.

    Uses one entity-scoped hybrid search. ``similarity`` is embedding cosine.
    Touched requires cosine >= ``TOUCHED_COSINE_THRESHOLD`` (0.72).
    ``likely_supersedes`` requires cosine >= ``SUPERSEDE_COSINE_THRESHOLD``
    (0.85) plus the functor and confidence gates. An FTS-only row has no
    cosine and clears neither threshold. The same recall is staged so
    ``guard_assertion_write`` does not search again on this claim.
    """
    from .graph_utils import extract_entity_ids

    similar = _entity_hybrid_search(conn, claim, entity_id, limit=20)
    recall = _take_last_recall()
    if recall is not None:
        stage_candidate_recall(entity_id, claim, recall)
    touched = [s for s in similar if s.similarity >= TOUCHED_COSINE_THRESHOLD]

    conf_rank = _CONFIDENCE_RANK.get(confidence, 0)
    incoming_functor = predicate_functor(predicate_form)
    likely_supersedes = [
        s.assertion_id
        for s in similar
        if s.similarity >= SUPERSEDE_COSINE_THRESHOLD
        and _CONFIDENCE_RANK.get(s.confidence, 0) <= conf_rank
        and incoming_functor is not None
        and predicate_functor(s.predicate_form) is not None
        and predicate_functor(s.predicate_form) == incoming_functor
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
    """One entity-local polarity conflict attached to an assertion write.

    ``similarity`` is embedding cosine. When embeddings are unavailable the
    value is 0.0 and ``retrieval_source`` is ``fts``; that 0.0 is not an FTS
    rank ratio and must not satisfy the C2 block threshold.
    """

    assertion_id: int
    claim: str
    confidence: str
    similarity: float
    retrieval_source: str = "vector"


@dataclass
class ContradictionResult:
    conflicts: list[ConflictDetail] = field(default_factory=list)
    safe: bool = True
    embeddings_unavailable: bool = False


def check_write_contradiction(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
    *,
    recall: CandidateRecall | None = None,
) -> ContradictionResult:
    """Detect entity-local contradictions before assertion commit.

    A conflict is a polarity clash against a candidate from the shared hybrid
    recall. Blocking cosine is applied by ``guard_assertion_write``: only
    cosine >= ``CONTRADICTION_SIMILARITY_THRESHOLD`` on a confirmed assertion
    can 409. When embeddings are unavailable, polarity hits are returned with
    ``retrieval_source=fts`` and similarity 0.0 so the guard flags and never
    blocks. Scoped to the entity (AGM G3), not global consistency. Passing
    ``recall`` does not run another hybrid search.
    """
    if recall is None:
        _entity_hybrid_search(conn, claim, entity_id, limit=20)
        recall = _take_last_recall()
    if recall is None:
        return ContradictionResult()

    conflicts: list[ConflictDetail] = []
    if recall.embeddings_unavailable:
        for row in recall.lexical_rows:
            if not row.claim or not detect_polarity_conflict(claim, row.claim):
                continue
            conflicts.append(
                ConflictDetail(
                    assertion_id=row.assertion_id,
                    claim=row.claim,
                    confidence=row.confidence,
                    similarity=0.0,
                    retrieval_source="fts",
                )
            )
        return ContradictionResult(
            conflicts=conflicts,
            safe=not conflicts,
            embeddings_unavailable=True,
        )

    for item in recall.scored:
        if not detect_polarity_conflict(claim, item.claim):
            continue
        conflicts.append(
            ConflictDetail(
                assertion_id=item.assertion_id,
                claim=item.claim,
                confidence=item.confidence,
                similarity=item.similarity,
                retrieval_source=item.retrieval_source,
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
    """Run the C2 cosine gate before INSERT. ``force=True`` allows the write.

    Event-log anchors (``thread:*``) are exempt: their rows are immutable turn
    records, not belief claims. Otherwise the guard consumes the recall staged
    by the assert nudge, or runs one ``_entity_hybrid_search`` when nothing was
    staged (HTTP create). Block only when cosine >=
    ``CONTRADICTION_SIMILARITY_THRESHOLD``, confidence is ``confirmed``, and
    ``detect_polarity_conflict`` is true. A lower cosine still flags. When
    embeddings are unavailable, polarity hits flag with ``retrieval_source=fts``
    and one log line ``c2_degraded=lexical``; that path never returns
    ``allowed=False``.
    """
    staged = take_staged_candidate_recall(entity_id, claim)
    if force or is_event_log_entity(entity_id):
        return WriteGuardResult(allowed=True)

    recall = staged
    if recall is None:
        _entity_hybrid_search(conn, claim, entity_id, limit=20)
        recall = _take_last_recall()
    result = check_write_contradiction(conn, entity_id, claim, recall=recall)
    if result.embeddings_unavailable and result.conflicts:
        logger.warning(
            "c2_degraded=lexical entity_id=%s retrieval_source=fts "
            "polarity_conflicts=%d",
            entity_id,
            len(result.conflicts),
        )
    if result.safe:
        return WriteGuardResult(allowed=True)

    # 409 only when cosine clears CONTRADICTION_SIMILARITY_THRESHOLD on a
    # confirmed assertion that also polarity-conflicts. A lower cosine, any
    # believed conflict, and every lexical-degraded hit proceed with
    # review_status='flagged'. Rank ratios are not consulted.
    blocking = [
        c
        for c in result.conflicts
        if c.confidence == "confirmed"
        and c.similarity >= CONTRADICTION_SIMILARITY_THRESHOLD
    ]
    if blocking:
        return WriteGuardResult(
            allowed=False,
            review_status="flagged",
            contradiction_warnings=result.conflicts,
            block_detail={
                "error": "contradiction_detected",
                "message": (
                    f"Contradicts {len(blocking)} confirmed assertion(s) "
                    f"on {entity_id} (similarity ≥ {CONTRADICTION_SIMILARITY_THRESHOLD})"
                ),
                "conflicts": [
                    {
                        "assertion_id": c.assertion_id,
                        "claim": c.claim,
                        "confidence": c.confidence,
                        "similarity": c.similarity,
                    }
                    for c in blocking
                ],
            },
        )

    return WriteGuardResult(
        allowed=True,
        review_status="flagged",
        contradiction_warnings=result.conflicts,
    )
