"""Predicate-functor gate for likely_supersedes (todo:write-discipline-dedup-precision).

Cross-family high-similarity pairs (e.g. status vs has_attribute on a hub skill)
must not surface as likely reassertions. Same-family pairs above the cosine
floor still qualify. Null predicate_form on either side excludes admission.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from .belief_guard import (
    SUPERSEDE_COSINE_THRESHOLD,
    SimilarAssertion,
    analyze_assertion_impact,
    predicate_functor,
)

_ENTITY = "agent_skill:corpus-map-authoring"
_STATUS_CLAIM = (
    "Corpus map authoring bundle regen status: next steps documented and done"
)
_HAS_ATTR_CLAIM = (
    "CANDIDATE SKILL REVISION guidance for agent_skill:corpus-map-authoring "
    "phase_3_guidance_specification attribute"
)


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _candidate(
    assertion_id: int,
    *,
    predicate_form: str | None,
    similarity: float = 0.90,
    confidence: str = "believed",
) -> SimilarAssertion:
    return SimilarAssertion(
        assertion_id=assertion_id,
        claim=_STATUS_CLAIM if predicate_form and predicate_form.startswith("status") else _HAS_ATTR_CLAIM,
        confidence=confidence,
        similarity=similarity,
        entity_id=_ENTITY,
        retrieval_source="both",
        predicate_form=predicate_form,
    )


@patch("cortex_store.belief_guard._entity_hybrid_search")
def test_cross_functor_pair_excluded_from_likely_supersedes(mock_search) -> None:
    """23553-shaped incoming vs 21935-shaped candidate — different functors."""
    mock_search.return_value = [
        _candidate(
            21935,
            predicate_form="status(corpus_map_authoring, done)",
        ),
    ]
    impact = analyze_assertion_impact(
        _conn(),
        _ENTITY,
        _HAS_ATTR_CLAIM,
        "believed",
        predicate_form=(
            "has_attribute(agent_skill:corpus-map-authoring, "
            "phase_3_guidance_specification)"
        ),
    )
    assert 21935 not in impact.likely_supersedes


@patch("cortex_store.belief_guard._entity_hybrid_search")
def test_same_functor_pair_still_in_likely_supersedes(mock_search) -> None:
    mock_search.return_value = [
        _candidate(
            1001,
            predicate_form="status(corpus_map_authoring, done)",
            confidence="suspected",
        ),
    ]
    impact = analyze_assertion_impact(
        _conn(),
        _ENTITY,
        _STATUS_CLAIM,
        "believed",
        predicate_form="status(corpus_map_authoring, in_progress)",
    )
    assert impact.likely_supersedes == [1001]


@patch("cortex_store.belief_guard._entity_hybrid_search")
def test_null_incoming_predicate_form_excludes_likely_supersedes(mock_search) -> None:
    mock_search.return_value = [
        _candidate(1002, predicate_form="status(corpus_map_authoring, done)"),
    ]
    impact = analyze_assertion_impact(
        _conn(), _ENTITY, _STATUS_CLAIM, "believed", predicate_form=None
    )
    assert impact.likely_supersedes == []


@patch("cortex_store.belief_guard._entity_hybrid_search")
def test_null_candidate_predicate_form_excludes_likely_supersedes(mock_search) -> None:
    mock_search.return_value = [
        _candidate(1003, predicate_form=None),
    ]
    impact = analyze_assertion_impact(
        _conn(),
        _ENTITY,
        _STATUS_CLAIM,
        "believed",
        predicate_form="status(corpus_map_authoring, in_progress)",
    )
    assert impact.likely_supersedes == []


def test_supersede_cosine_threshold_unchanged() -> None:
    assert SUPERSEDE_COSINE_THRESHOLD == 0.85


def test_predicate_functor_extracts_head() -> None:
    assert predicate_functor("status(corpus_map_authoring, done)") == "status"
    assert (
        predicate_functor(
            "has_attribute(agent_skill:corpus-map-authoring, phase_3)"
        )
        == "has_attribute"
    )
    assert predicate_functor(None) is None
    assert predicate_functor("") is None
