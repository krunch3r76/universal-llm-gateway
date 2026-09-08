"""Unit tests for hub WATERMARK parsing."""

from __future__ import annotations

from agent_bus_store.continuity_watermark import (
    SEEDED_BY,
    hub_entity_id,
    parse_watermark,
)


def test_hub_entity_id() -> None:
    assert hub_entity_id("root-abc") == "document:root-abc-continuity"


def test_parse_watermark_newest_id_wins() -> None:
    rows = [
        {
            "id": 10,
            "seeded_by": SEEDED_BY,
            "claim": "WATERMARK: consolidated_through=lane-a#3",
        },
        {
            "id": 42,
            "seeded_by": SEEDED_BY,
            "claim": "WATERMARK: consolidated_through=lane-b#99",
        },
    ]
    result = parse_watermark(rows)
    assert result is not None
    assert result["assertion_id"] == 42
    assert result["thread"] == "lane-b"
    assert result["turn"] == 99


def test_parse_watermark_ignores_wrong_seeded_by() -> None:
    rows = [
        {
            "id": 100,
            "seeded_by": "manual-entry",
            "claim": "WATERMARK: consolidated_through=spoof#1",
        },
        {
            "id": 5,
            "seeded_by": SEEDED_BY,
            "claim": "WATERMARK: consolidated_through=real#7",
        },
    ]
    result = parse_watermark(rows)
    assert result is not None
    assert result["thread"] == "real"
    assert result["turn"] == 7


def test_parse_watermark_rejects_malformed_claims() -> None:
    rows = [
        {
            "id": 1,
            "seeded_by": SEEDED_BY,
            "claim": "WATERMARK: consolidated_through=missing-hash",
        },
        {
            "id": 2,
            "seeded_by": SEEDED_BY,
            "claim": "NOTE: consolidated_through=lane#5",
        },
        {
            "id": 3,
            "seeded_by": SEEDED_BY,
            "claim": "WATERMARK: consolidated_through=bad-turn#abc",
        },
    ]
    assert parse_watermark(rows) is None


def test_parse_watermark_empty_rows() -> None:
    assert parse_watermark([]) is None
