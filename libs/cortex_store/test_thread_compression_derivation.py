"""thread_compression derivation_type validation."""

from __future__ import annotations

from cortex_store.assertion_quality import validate_assertion
from cortex_store.models import AssertionCreate


def _body(**overrides: object) -> AssertionCreate:
    base = {
        "entity_id": "thread:anchor-1",
        "claim": "archive summary: prior context.",
        "confidence": "confirmed",
        "evidence": "thread compaction",
        "derivation_type": "thread_compression",
        "evidence_uris": ["workspaces://ulg/.runtime/t/turn_0001.json"],
        "observed_at": "2026-06-02T00:00:00Z",
    }
    base.update(overrides)
    return AssertionCreate(**base)  # type: ignore[arg-type]


def test_thread_compression_requires_evidence_uris() -> None:
    result = validate_assertion(_body(evidence_uris=[]))
    assert result.rejected
    assert any(d.field == "evidence_uris" for d in result.hard_reject)


def test_thread_compression_rejects_chunk_id() -> None:
    result = validate_assertion(_body(chunk_id="abc-0"))
    assert result.rejected
    assert any(d.field == "chunk_id" for d in result.hard_reject)


def test_thread_compression_valid_with_uris_only() -> None:
    result = validate_assertion(
        _body(reasoning_summary="compacted from workspace turn artifacts")
    )
    assert not result.rejected
    assert result.route_to_staging is False


def test_nondocument_evidence_uris_without_chunk_commits() -> None:
    body = AssertionCreate(
        entity_id="service:cortex-api",
        claim="agent synthesis citing a prior assertion.",
        confidence="believed",
        evidence="derived from prior session context and a cited assertion row",
        derivation_type="inference",
        evidence_uris=["cortex://assertion/20214"],
        reasoning_summary="cited the prior row directly; no RAG chunk applies",
        observed_at="2026-06-23T00:00:00Z",
    )
    result = validate_assertion(body)
    assert result.route_to_staging is False


def test_quotation_without_chunk_still_hard_rejects() -> None:
    result = validate_assertion(
        _body(derivation_type="quotation", evidence_uris=["cortex://doc/x"])
    )
    assert result.rejected
    assert any(d.field == "chunk_id" for d in result.hard_reject)


def test_missing_reasoning_summary_still_stages() -> None:
    body = AssertionCreate(
        entity_id="service:cortex-api",
        claim="x.",
        confidence="believed",
        evidence="e",
        derivation_type="inference",
        evidence_uris=["cortex://assertion/1"],
        observed_at="2026-06-23T00:00:00Z",
    )
    assert validate_assertion(body).route_to_staging is True


def test_low_quality_score_still_stages() -> None:
    body = AssertionCreate(
        entity_id="service:cortex-api",
        claim="x.",
        confidence="believed",
        evidence="short",
        derivation_type="inference",
        reasoning_summary="present but provenance incomplete so score stays low",
        observed_at="2026-06-23T00:00:00Z",
    )
    result = validate_assertion(body)
    assert result.quality_score < 0.7
    assert result.route_to_staging is True
