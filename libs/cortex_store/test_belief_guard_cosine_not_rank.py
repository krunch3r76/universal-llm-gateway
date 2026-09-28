"""C1 similarity is embedding cosine, not a within-set FTS rank ratio.

The top FTS row of any non-empty result is identically 1.0 under
``abs(rank)/max(abs(rank))``. That figure must not present an unrelated
candidate as a confident duplicate, and the assert nudge must not tell the
caller to re-run the impact check it just ran.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from .belief_guard import (
    TOUCHED_COSINE_THRESHOLD,
    ImpactAnalysis,
    SimilarAssertion,
    _entity_hybrid_search,
    analyze_assertion_impact,
)
from .models.assertions import ImpactAnalysisRequest
from .routes.graph import analyze_impact_semantic
from .write_discipline_nudge import build_assert_nudge

_ENTITY = "service:cortex"
_UNRELATED = "hop cadence on this path is nil and stays nil"
_INCOMING = "service cortex duplicate advisory reports embedding cosine"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _stats_conn() -> sqlite3.Connection:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT);
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT,
            superseded_by INTEGER
        );
        CREATE TABLE relationships (
            active INTEGER,
            from_entity TEXT,
            to_entity TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO entities (id, type) VALUES (?, ?)",
        (_ENTITY, "service"),
    )
    return conn


def _fts_row(
    assertion_id: int,
    *,
    rank: float,
    claim: str = _UNRELATED,
    predicate_form: str | None = "status(service_cortex, recorded)",
) -> dict:
    return {
        "id": assertion_id,
        "claim": claim,
        "confidence": "believed",
        "entity_id": _ENTITY,
        "predicate_form": predicate_form,
        "rank": rank,
    }


@patch("cortex_store.belief_guard._entity_vector_search")
@patch("cortex_store.belief_guard._entity_fts_search")
def test_unrelated_top_fts_row_is_not_similarity_one(mock_fts, mock_vector) -> None:
    """Singleton FTS result would be rank-ratio 1.00. Cosine 0.11 must win."""
    mock_fts.return_value = [_fts_row(35678, rank=-12.4)]
    mock_vector.return_value = [
        {"assertion_id": 35678, "cosine_similarity": 0.11},
    ]

    found = _entity_hybrid_search(_conn(), _INCOMING, _ENTITY)
    assert len(found) == 1
    assert found[0].assertion_id == 35678
    assert found[0].similarity == 0.11
    assert found[0].similarity != 1.0

    impact = analyze_assertion_impact(
        _conn(),
        _ENTITY,
        _INCOMING,
        "believed",
        predicate_form="status(service_cortex, proposed)",
    )
    assert impact.touched_assertions == []
    assert impact.likely_supersedes == []
    assert found[0].similarity < TOUCHED_COSINE_THRESHOLD


@patch("cortex_store.belief_guard._entity_vector_search", return_value=[])
@patch("cortex_store.belief_guard._entity_fts_search")
def test_fts_only_top_row_is_absent_not_similarity_one(mock_fts, _mock_vector) -> None:
    mock_fts.return_value = [_fts_row(35678, rank=-12.4)]
    found = _entity_hybrid_search(_conn(), _INCOMING, _ENTITY)
    assert found == []
    impact = analyze_assertion_impact(_conn(), _ENTITY, _INCOMING, "believed")
    assert impact.touched_assertions == []
    assert impact.likely_supersedes == []


@patch("cortex_store.belief_guard._entity_vector_search")
@patch("cortex_store.belief_guard._entity_fts_search")
def test_order_follows_cosine_not_fts_rank(mock_fts, mock_vector) -> None:
    """Best FTS rank used to force similarity 1.0 and sort first."""
    mock_fts.return_value = [
        _fts_row(1, rank=-20.0, claim="best fts rank, weak cosine"),
        _fts_row(2, rank=-2.0, claim="worse fts rank, stronger cosine"),
    ]
    mock_vector.return_value = [
        {"assertion_id": 1, "cosine_similarity": 0.20},
        {"assertion_id": 2, "cosine_similarity": 0.50},
    ]
    found = _entity_hybrid_search(_conn(), _INCOMING, _ENTITY)
    assert [item.assertion_id for item in found] == [2, 1]
    assert [item.similarity for item in found] == [0.5, 0.2]


@patch("cortex_store.belief_guard._entity_vector_search")
@patch("cortex_store.belief_guard._entity_fts_search")
def test_cosine_at_supersede_floor_still_binds(mock_fts, mock_vector) -> None:
    mock_fts.return_value = [
        _fts_row(
            1001,
            rank=-0.01,
            claim=_INCOMING,
            predicate_form="status(service_cortex, recorded)",
        )
    ]
    mock_vector.return_value = [
        {"assertion_id": 1001, "cosine_similarity": 0.91},
    ]
    impact = analyze_assertion_impact(
        _conn(),
        _ENTITY,
        _INCOMING,
        "believed",
        predicate_form="status(service_cortex, proposed)",
    )
    assert impact.likely_supersedes == [1001]
    assert impact.touched_assertions[0].similarity == 0.91


@patch("cortex_store.belief_guard._entity_vector_search")
@patch("cortex_store.belief_guard._entity_fts_search")
def test_nudge_omits_unrelated_top_candidate(mock_fts, mock_vector) -> None:
    mock_fts.return_value = [_fts_row(35678, rank=-12.4)]
    mock_vector.return_value = [
        {"assertion_id": 35678, "cosine_similarity": 0.11},
    ]
    nudge = build_assert_nudge(_stats_conn(), _ENTITY, _INCOMING, "believed")
    assert nudge is None


@patch("cortex_store.write_discipline_nudge.analyze_assertion_impact")
def test_nudge_does_not_recommend_rerunning_analyze_impact(mock_impact) -> None:
    mock_impact.return_value = ImpactAnalysis(
        touched_assertions=[
            SimilarAssertion(
                assertion_id=35678,
                claim=_UNRELATED,
                confidence="believed",
                similarity=0.80,
                entity_id=_ENTITY,
                retrieval_source="both",
                predicate_form="has_attribute(service:cortex, note)",
            )
        ],
        likely_supersedes=[],
    )
    nudge = build_assert_nudge(
        _stats_conn(),
        _ENTITY,
        _INCOMING,
        "believed",
        predicate_form="status(service_cortex, proposed)",
    )
    assert nudge is not None
    text = " ".join(nudge["suggestions"])
    assert "analyze_impact" not in text
    assert "review assertion #35678 before assert" in text
    assert nudge["analyze_impact"]["top_similarity"] == 0.80
    assert "top cosine=0.80" in text


@patch("cortex_store.write_discipline_nudge.analyze_assertion_impact")
def test_likely_supersedes_suggestion_names_supersede_not_analyze(mock_impact) -> None:
    mock_impact.return_value = ImpactAnalysis(
        touched_assertions=[
            SimilarAssertion(
                assertion_id=1001,
                claim=_INCOMING,
                confidence="suspected",
                similarity=0.91,
                entity_id=_ENTITY,
                retrieval_source="both",
                predicate_form="status(service_cortex, recorded)",
            )
        ],
        likely_supersedes=[1001],
    )
    nudge = build_assert_nudge(
        _stats_conn(),
        _ENTITY,
        _INCOMING,
        "believed",
        predicate_form="status(service_cortex, proposed)",
    )
    assert nudge is not None
    text = " ".join(nudge["suggestions"])
    assert "analyze_impact" not in text
    assert "supersede(...)" in text
    assert "likely_supersedes=[1001]" in text


@patch("cortex_store.belief_guard._entity_vector_search")
@patch("cortex_store.belief_guard._entity_fts_search")
@patch("cortex_store.routes.graph.db_query", return_value=[{"id": _ENTITY}])
@patch("cortex_store.routes.graph.cortex_conn")
def test_mcp_analyze_impact_omits_unrelated_top_candidate(
    mock_conn, _mock_query, mock_fts, mock_vector
) -> None:
    mock_conn.return_value = _conn()
    mock_fts.return_value = [_fts_row(35678, rank=-12.4)]
    mock_vector.return_value = [
        {"assertion_id": 35678, "cosine_similarity": 0.11},
    ]
    response = analyze_impact_semantic(
        ImpactAnalysisRequest(entity_id=_ENTITY, claim=_INCOMING)
    )
    assert response.touched_assertions == []
    assert response.likely_supersedes == []
    assert response.impact_score == 0.0
