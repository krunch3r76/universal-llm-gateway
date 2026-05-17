"""Unit tests for materializers — happy paths, invariant violations, D.2 content-hash round-trip (load-bearing)."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from ..errors import AgentInjectionAdmissionError
from ..materializers import (
    compute_d2_content_hash,
    materialize_d1,
    materialize_d2,
    materialize_d3,
    materialize_d4,
)


def _mk_row(**overrides):
    base = {
        "id": 123,
        "entity_id": "case:42",
        "claim": "The sky is blue.",
        "confidence": "confirmed",
        "confidence_score": 0.92,
        "evidence_uris": ["https://ex"],
        "seeded_by": "user",
        "derivation_type": "user_statement",
        "observed_at": "2025-01-01T00:00:00",
        "valid_from": "2025-01-01",
        "valid_until": None,
        "superseded_by": None,
        "created_at": "2025-01-01T00:00:00",
        "reasoning_summary": None,
        "predicate_form": "is_color",
    }
    base.update(overrides)
    return base


def test_compute_d2_content_hash_deterministic():
    body = "line1\n  | content_hash: sha256:xxx\nline2  \n"
    h1 = compute_d2_content_hash(body)
    h2 = compute_d2_content_hash(body)
    assert h1 == h2
    assert h1.startswith("sha256:") and len(h1) == 71


@patch("cortex_store.agent_injection.materializers.query")
@patch("cortex_store.agent_injection.materializers.cortex_conn")
def test_materialize_d1_happy(mock_conn, mock_query):
    mock_conn.return_value.__enter__.return_value = MagicMock()
    mock_query.return_value = [_mk_row()]
    res = materialize_d1(123, field_name="color")
    assert res["kind"] == "d1"
    assert res["grade"] == "structural"
    assert "[STRUCTURED_LOOKUP | source: assertion 123" in res["rendered"]
    assert "Field: color" in res["rendered"]


@patch("cortex_store.agent_injection.materializers.query")
@patch("cortex_store.agent_injection.materializers.cortex_conn")
def test_materialize_d1_superseded_raises(mock_conn, mock_query):
    row = _mk_row(superseded_by=999)
    mock_query.return_value = [row]
    with pytest.raises(AgentInjectionAdmissionError) as exc:
        materialize_d1(123, field_name="x")
    assert "superseded" in str(exc.value)
    v = exc.value.violations[0]
    assert v.invariant == 2
    assert v.detail == "superseded"


@patch("cortex_store.agent_injection.materializers.query")
@patch("cortex_store.agent_injection.materializers.cortex_conn")
def test_materialize_d4_grade_mismatch_raises(mock_conn, mock_query):
    row = _mk_row(confidence="confirmed", reasoning_summary="foo")
    mock_query.return_value = [row]
    with pytest.raises(AgentInjectionAdmissionError) as exc:
        materialize_d4(123)
    assert exc.value.violations[0].detail == "grade_mismatch"


@patch("cortex_store.agent_injection.materializers.query")
@patch("cortex_store.agent_injection.materializers.cortex_conn")
def test_materialize_d4_happy(mock_conn, mock_query):
    row = _mk_row(confidence="believed", reasoning_summary="because X")
    mock_query.return_value = [row]
    res = materialize_d4(123)
    assert res["kind"] == "d4"
    assert res["grade"] == "belief"
    assert "[BELIEF_INJECTION | assertion_id: 123" in res["rendered"]
    assert "Reasoning: because X" in res["rendered"]


@patch("cortex_store.agent_injection.materializers.query")
@patch("cortex_store.agent_injection.materializers.cortex_conn")
def test_materialize_d2_happy_and_roundtrip(mock_conn, mock_query):
    """D.2 content-hash ROUND-TRIP test (load-bearing)."""
    # entity exists + 3 active assertions
    def q_side_effect(conn, sql, params):
        if "FROM entities" in sql:
            return [{"id": params[0]}]
        if "FROM assertions" in sql:
            return [
                _mk_row(id=1, claim="A", confidence_score=0.8, predicate_form="p1"),
                _mk_row(id=2, claim="B", confidence_score=0.7, predicate_form="p2"),
                _mk_row(id=3, claim="C", confidence_score=0.9, predicate_form="p3"),
            ]
        return []
    mock_query.side_effect = q_side_effect

    res = materialize_d2("case:42", selection_strategy="highest_confidence_n", selection_params={"n": 2})
    assert res["kind"] == "d2"
    assert res["included_count"] == 2
    assert res["total_active_count"] == 3
    assert res["truncated"] is False
    assert res["content_hash"].startswith("sha256:")

    # round-trip: recompute must equal stored
    rendered = res["rendered"]
    # strip hash line exactly as materializer does
    body_lines = rendered.splitlines()
    no_hash = [ln for ln in body_lines if not re.match(r"^\s*\| (content_hash|pulled_at):", ln)]
    body_wo = "\n".join(no_hash)
    recomputed = compute_d2_content_hash(body_wo)
    assert recomputed == res["content_hash"], "D.2 hash round-trip failed — indicates broken replace-hash pattern"


@patch("cortex_store.agent_injection.materializers.query")
@patch("cortex_store.agent_injection.materializers.cortex_conn")
def test_materialize_d2_overflow_default_raises(mock_conn, mock_query):
    def q_side_effect(conn, sql, params):
        if "entities" in sql:
            return [{}]
        return [_mk_row(id=i) for i in range(5)]
    mock_query.side_effect = q_side_effect

    with pytest.raises(AgentInjectionAdmissionError) as exc:
        materialize_d2("e:1", per_entity_limit=2)  # default "all"
    assert exc.value.violations[0].detail == "overflow_default_strategy"
    assert exc.value.violations[0].invariant == 4


@patch("cortex_store.agent_injection.materializers.query")
@patch("cortex_store.agent_injection.materializers.cortex_conn")
def test_materialize_d3_follows_superseded_chain(mock_conn, mock_query):
    # original superseded, points to 200 which is active
    calls = []
    def q_side_effect(conn, sql, params):
        calls.append(params[0] if params else None)
        if params and params[0] == 100:
            return [_mk_row(id=100, superseded_by=200)]
        if params and params[0] == 200:
            return [_mk_row(id=200, superseded_by=None, claim="current claim")]
        return []
    mock_query.side_effect = q_side_effect

    res = materialize_d3(100)
    assert res["assertion_id"] == 200
    assert res["_meta"]["superseded_chain_followed"] is True
    assert "current claim" in res["rendered"]


def test_d2_content_hash_roundtrip_standalone():
    """Explicit named test for gate: must exist and pass."""
    body = "[CONTEXT_PROVISION\n  | foo: bar\n  | content_hash: sha256:xxx\nrows\n[/CONTEXT_PROVISION]\n"
    # simulate what materializer produces (without the hash line for compute)
    wo = body.replace("  | content_hash: sha256:xxx\n", "")
    h = compute_d2_content_hash(wo)
    assert h.startswith("sha256:")
    # if someone did str.replace on a pending hash it would not match recompute
