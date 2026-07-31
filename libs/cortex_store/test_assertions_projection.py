"""Tests for assertions list intent=summary projection (friction #16896)."""

from __future__ import annotations

import json

from cortex_store.dispatch_ops.ops_assertions import (
    _op_assertions,
    _op_frictions,
    _op_review_queue,
)
from cortex_store.models import AssertionListSummaryItem
from cortex_store.routes.assertions._list import _list_assertions_summary
from cortex_store.routes.assertions._shared import _truncate_claim


def test_truncate_claim_truncates_long_claim() -> None:
    claim = "x" * 300
    truncated = _truncate_claim(claim)
    assert len(truncated) == 200
    assert truncated.endswith("…")


def test_summary_item_omits_heavy_fields() -> None:
    long_claim = "cortex projection " + ("detail " * 40)
    item = AssertionListSummaryItem(
        id=1,
        entity_id="decision:cursor-sdk-generate-peer",
        claim=_truncate_claim(long_claim),
        confidence="confirmed",
        review_status="committed",
        has_evidence_uris=True,
        has_enrichment=True,
        _deepen="cortex(tool=assertion_get, assertion_id=1)",
    )
    wire = item.model_dump(mode="json", by_alias=True)
    assert "prospective_summary" not in wire
    assert "events_json" not in wire
    assert "evidence" not in wire
    assert "predicate_form" not in wire
    assert len(item.claim) <= 200
    assert wire["_deepen"].startswith("cortex(tool=assertion_get")


def test_summary_row_wire_byte_budget() -> None:
    """12 dense summary rows should stay well under the 16KB friction threshold."""
    rows = []
    for i in range(12):
        rows.append(
            AssertionListSummaryItem(
                id=16890 + i,
                entity_id="decision:cursor-sdk-generate-peer",
                claim=_truncate_claim("ratification browse " + ("payload " * 30)),
                confidence="confirmed",
                review_status="committed",
                derivation_type="inference",
                observed_at="2026-06-11T00:00:00Z",
                has_evidence_uris=True,
                has_enrichment=True,
                _deepen=f"cortex(tool=assertion_get, assertion_id={16890 + i})",
            )
        )
    payload = {
        "intent": "summary",
        "items": [r.model_dump(mode="json", by_alias=True) for r in rows],
    }
    assert len(json.dumps(payload)) < 16_000


def test_has_booleans_derived_from_row_fields(monkeypatch) -> None:
    captured_sql: list[str] = []

    def fake_query(conn: object, sql: str, params: tuple[object, ...]) -> list[dict]:
        captured_sql.append(sql)
        return [
            {
                "id": 42,
                "entity_id": "todo:example",
                "claim": "short claim",
                "confidence": "believed",
                "review_status": "committed",
                "derivation_type": "inference",
                "observed_at": "2026-06-11T00:00:00Z",
                "superseded_by": None,
                "evidence_uris": '["cortex://x"]',
                "prospective_summary": "enriched",
                "events_json": None,
                "reasoning_summary": None,
                "attributes": None,
            }
        ]

    monkeypatch.setattr(
        "cortex_store.routes.assertions._list.query",
        fake_query,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._list.cortex_conn",
        lambda: (_ for _ in ()).throw(AssertionError("unused")),
    )

    class _ConnCtx:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.routes.assertions._list.cortex_conn",
        lambda: _ConnCtx(),
    )

    result = _list_assertions_summary(
        entity_id="todo:example",
        entity_id_prefix=None,
        claim_filter=None,
        seeded_by=None,
        confidence=None,
        review_status=None,
        superseded=None,
        entity_type=None,
        entity_type_in=None,
        entity_type_exclude=None,
        valid_at=None,
        known_at=None,
        limit=5,
        include_compaction_pointers=False,
    )
    assert result.intent == "summary"
    assert len(result.items) == 1
    item = result.items[0]
    assert item.has_evidence_uris is True
    assert item.has_enrichment is True
    assert item.model_dump(mode="json", by_alias=True)["_deepen"] == (
        "cortex(tool=assertion_get, assertion_id=42)"
    )
    assert "_ASSERTION_SUMMARY_COLS" not in captured_sql[0]
    assert "prospective_summary" in captured_sql[0]


def test_op_assertions_defaults_to_summary(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"intent": "summary", "items": []}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._list_assertions_impl",
        fake_list,
    )
    result = _op_assertions(limit=12)
    assert captured["intent"] == "summary"
    assert "_next" in result


def test_op_assertions_full_has_no_summary_next(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "intent": "full",
            "items": [
                {"id": 1, "claim": "x", "confidence": "confirmed", "created_at": "t"}
            ],
        }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._list_assertions_impl",
        fake_list,
    )
    result = _op_assertions(intent="full")
    assert captured["intent"] == "full"
    assert "_next" not in result


def test_op_frictions_defaults_summary(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "intent": "summary",
            "items": [
                {
                    "id": 1,
                    "claim": "[tool_error] x",
                    "confidence": "confirmed",
                    "_deepen": "cortex(tool=assertion_get, assertion_id=1)",
                }
            ],
        }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._list_assertions_impl",
        fake_list,
    )
    result = _op_frictions(category="tool_error")
    assert captured["intent"] == "summary"
    assert captured["limit"] == 7
    assert "Deepen one row" in result["_next"]


def test_op_frictions_explicit_full(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "intent": "full",
            "items": [
                {
                    "id": 1,
                    "claim": "[tool_error] x",
                    "confidence": "confirmed",
                    "created_at": "t",
                    "evidence": "blob",
                }
            ],
        }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._list_assertions_impl",
        fake_list,
    )
    result = _op_frictions(category="tool_error", intent="full")
    assert captured["intent"] == "full"
    assert "Deepen one row" not in result["_next"]


def test_op_review_queue_pins_full(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.append(dict(kwargs))
        return {
            "intent": "full",
            "items": [
                {
                    "id": 1,
                    "claim": "review me",
                    "confidence": "suspected",
                    "created_at": "t",
                    "review_notes": "note",
                }
            ],
        }

    def fake_entities(**kwargs: object) -> dict[str, object]:
        return {"items": []}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._list_assertions_impl",
        fake_list,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._op_entities",
        fake_entities,
    )
    _op_review_queue(limit=5)
    assert len(captured) == 3
    assert all(call["intent"] == "full" for call in captured)
