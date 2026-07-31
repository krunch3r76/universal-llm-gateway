"""Tests for cortex search intent=summary projection (todo:fts-search-projection-mode)."""

from __future__ import annotations

import json

from cortex_store.routes.assertions._search import (
    _summary_items,
    _truncate_claim,
    search_assertions,
)


def test_truncate_claim_truncates_long_claim() -> None:
    claim = "x" * 300
    truncated = _truncate_claim(claim)
    assert len(truncated) == 200
    assert truncated.endswith("…")


def test_summary_items_omit_heavy_fields() -> None:
    long_claim = "cortex projection " + ("detail " * 40)
    fused = [
        {
            "id": 1,
            "entity_id": "todo:fts-search-projection-mode",
            "entity_name": "FTS projection",
            "claim": long_claim,
            "confidence": "confirmed",
            "review_status": "committed",
            "combmax_score": 0.91,
            "retrieval_source": "fts",
            "prospective_summary": "should not appear on wire",
            "events_json": '[{"event":"x"}]',
            "evidence": "heavy evidence blob",
        }
    ]
    items = _summary_items(fused)
    assert len(items) == 1
    item = items[0]
    wire = json.dumps(item.model_dump(mode="json"))
    assert "prospective_summary" not in wire
    assert "events_json" not in wire
    assert "evidence" not in wire
    assert "snippet" not in wire
    assert len(item.claim) <= 200


def test_search_assertions_whitespace_query_returns_empty_summary() -> None:
    from cortex_store.models import AssertionSearchResult

    result = search_assertions(q="   ", intent="summary")
    assert result.intent == "summary"
    assert result.items == []
    assert "section_manifest" not in AssertionSearchResult.model_fields
