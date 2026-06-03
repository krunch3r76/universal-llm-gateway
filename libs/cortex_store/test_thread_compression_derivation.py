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
    result = validate_assertion(_body())
    assert not result.rejected
