"""Frozen-snapshot regression for friction-13633 card ranking (Option E + A).

Does NOT read live ``person:kaywan-mansubi`` — post-incident recency would
yield a false PASS. Fixture pins assertion set at pre-2026-06-08T07:10Z.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.card import (
    _fetch_main_top_k,
    _merge_current_status_slot,
    get_entity_card,
)
from cortex_store.compaction import POINTER_SQL_LIKE, SUMMARY_SQL_LIKE
from cortex_store.db import query

_FIXTURE_ENTITY = "person:snapshot-fixture"
_FROZEN_CUTOFF = "2026-06-08T07:10:00Z"
_STATUS_PREDICATE = f"status({_FIXTURE_ENTITY}, unemployed, current)"


def _insert_ranked_assertion(
    conn: sqlite3.Connection,
    *,
    claim: str,
    created_at: str,
    entrenchment_score: float,
    predicate_form: str | None = None,
    confidence: str = "believed",
    prospective_summary: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "entrenchment_score, prospective_summary, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _FIXTURE_ENTITY,
            claim,
            confidence,
            predicate_form,
            entrenchment_score,
            prospective_summary,
            created_at,
            created_at,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


@pytest.fixture()
def snapshot_fixture(migrated_conn: sqlite3.Connection) -> dict[str, int]:
    """Seed the frozen friction-13633 assertion set (≥10 rows, top_k=7 eviction)."""
    conn = migrated_conn
    insert_entity(
        conn, entity_id=_FIXTURE_ENTITY, entity_type="person", name="Snapshot"
    )

    a1 = _insert_ranked_assertion(
        conn,
        claim="Kaywan is currently unemployed since CVS (Aug 2025).",
        created_at="2026-05-01T12:00:00Z",
        entrenchment_score=0.6,
        predicate_form=_STATUS_PREDICATE,
        confidence="confirmed",
        prospective_summary="current employment status",
    )
    high_e_ids: list[int] = []
    for i, label in enumerate(
        (
            "State Farm",
            "Uber",
            "Google",
            "Meta",
            "Apple",
            "Amazon",
            "Netflix",
            "Tesla",
            "Microsoft",
            "Adobe",
        )
    ):
        high_e_ids.append(
            _insert_ranked_assertion(
                conn,
                claim=f"Worked at {label} in a prior role.",
                created_at=f"2026-06-0{(5 + (i % 3)):01}T{10 + i:02d}:00:00Z",
                entrenchment_score=1.0,
                predicate_form=f"worked_at({_FIXTURE_ENTITY}, {label.lower().replace(' ', '_')})",
            )
        )
    return {"a1": a1, "high_e": high_e_ids}


def test_e_surfaces_current_status(
    migrated_conn: sqlite3.Connection,
    snapshot_fixture: dict[str, int],
) -> None:
    card = get_entity_card(migrated_conn, entity_id=_FIXTURE_ENTITY, top_k=7)
    surfaced_ids = {a["id"] for a in card["top_k_assertions"]}
    assert snapshot_fixture["a1"] in surfaced_ids


def test_a_only_insufficient(
    migrated_conn: sqlite3.Connection,
    snapshot_fixture: dict[str, int],
) -> None:
    a1_id = snapshot_fixture["a1"]
    main = _fetch_main_top_k(migrated_conn, entity_id=_FIXTURE_ENTITY, top_k=7)
    main_ids = {int(r["id"]) for r in main}
    assert a1_id not in main_ids, (
        "Option A alone must not surface the current-status row"
    )

    recency_rows = query(
        migrated_conn,
        f"SELECT id FROM assertions WHERE entity_id = ? AND superseded_by IS NULL "
        "ORDER BY "
        "  (CASE WHEN LOWER(claim) LIKE LOWER(?) THEN 0 ELSE 1 END) ASC, "
        "  (CASE WHEN LOWER(claim) LIKE LOWER(?) THEN 1 ELSE 0 END) ASC, "
        "  created_at DESC LIMIT 7",
        (_FIXTURE_ENTITY, SUMMARY_SQL_LIKE, POINTER_SQL_LIKE),
    )
    recency_ids = {int(r["id"]) for r in recency_rows}
    assert a1_id not in recency_ids, (
        "recency-only ordering must also evict current-status row"
    )


def test_no_current_status_is_noop(migrated_conn: sqlite3.Connection) -> None:
    insert_entity(migrated_conn, entity_id="todo:no-status", entity_type="todo")
    ids: list[int] = []
    for i in range(8):
        cur = migrated_conn.execute(
            "INSERT INTO assertions (entity_id, claim, confidence, entrenchment_score, "
            "predicate_form, created_at) VALUES (?, ?, 'believed', ?, ?, ?)",
            (
                "todo:no-status",
                f"Operative claim {i}",
                float(i) / 10,
                f"describes(todo:no-status, item_{i})",
                f"2026-06-0{i + 1:01}T12:00:00Z",
            ),
        )
        migrated_conn.commit()
        ids.append(int(cur.lastrowid or 0))

    main = _fetch_main_top_k(migrated_conn, entity_id="todo:no-status", top_k=7)
    merged = _merge_current_status_slot(main, None, top_k=7)
    assert [int(r["id"]) for r in merged] == [int(r["id"]) for r in main]


def test_predicate_summary_input_unchanged_by_e(
    migrated_conn: sqlite3.Connection,
    snapshot_fixture: dict[str, int],
) -> None:
    with patch("cortex_store.card.aggregate_predicate_summary") as mock_agg:
        mock_agg.return_value = ""
        get_entity_card(migrated_conn, entity_id=_FIXTURE_ENTITY, top_k=7)
        mock_agg.assert_called_once()
        passed_rows = mock_agg.call_args.kwargs["top_k_assertions"]
        main = _fetch_main_top_k(migrated_conn, entity_id=_FIXTURE_ENTITY, top_k=7)
        assert [int(r["id"]) for r in passed_rows] == [int(r["id"]) for r in main]


def test_debug_surfaces_entrenchment_and_prospective_summary(
    migrated_conn: sqlite3.Connection,
    snapshot_fixture: dict[str, int],
) -> None:
    card = get_entity_card(
        migrated_conn, entity_id=_FIXTURE_ENTITY, top_k=7, debug=True
    )
    assert card["debug"] is not None
    assert any(
        a.get("entrenchment_score") is not None for a in card["top_k_assertions"]
    )
    assert card["debug"]["prospective_summaries"] is not None
