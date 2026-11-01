"""Card read-path temporal eligibility — current projection before LIMIT."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.card import _fetch_main_top_k, get_entity_card
from cortex_store.card_temporal import (
    assertion_interval_contains,
    install_card_temporal_function,
)
from cortex_store.subgraph_template import render_root_card_markdown

_FROZEN_AS_OF = "2026-10-31T12:00:00+00:00"
_40883_UNTIL = "2026-10-30T22:15:59.844900+00:00"


def _insert_temporal_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
    entrenchment_score: float = 0.5,
    valid_from: str | None = None,
    valid_until: str | None = None,
    predicate_form: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "entrenchment_score, valid_from, valid_until, created_at, updated_at) "
        "VALUES (?, ?, 'believed', ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            claim,
            predicate_form,
            entrenchment_score,
            valid_from,
            valid_until,
            _FROZEN_AS_OF,
            _FROZEN_AS_OF,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


@pytest.mark.parametrize(
    ("valid_from", "valid_until", "as_of", "expected"),
    [
        (None, None, _FROZEN_AS_OF, True),
        (_FROZEN_AS_OF, None, _FROZEN_AS_OF, True),
        (None, "2026-11-01T00:00:00+00:00", _FROZEN_AS_OF, True),
        ("2026-11-01T00:00:00+00:00", None, _FROZEN_AS_OF, False),
        (None, _FROZEN_AS_OF, _FROZEN_AS_OF, False),
        ("2026-10-31", "2026-11-01", "2026-10-31T00:00:00+00:00", True),
        ("2026-10-31T12:00:00Z", "2026-11-01T12:00:00+00:00", _FROZEN_AS_OF, True),
        ("2026-10-31T12:00:00.000001+00:00", None, _FROZEN_AS_OF, False),
        ("2026-11-02", "2026-11-01", _FROZEN_AS_OF, False),
        ("not-a-date", None, _FROZEN_AS_OF, False),
    ],
)
def test_assertion_interval_contains_parametric(
    valid_from: str | None,
    valid_until: str | None,
    as_of: str,
    expected: bool,
) -> None:
    assert assertion_interval_contains(valid_from, valid_until, as_of) is expected


def test_expired_high_rank_absent_from_top_k(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-topk"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Expired readiness window",
        entrenchment_score=9.9,
        valid_until=_40883_UNTIL,
    )
    valid_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Still valid lower rank",
        entrenchment_score=0.1,
        valid_until="2027-01-01T00:00:00+00:00",
    )
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        main = _fetch_main_top_k(conn, entity_id=entity_id, top_k=7, as_of=_FROZEN_AS_OF)
    ids = [int(r["id"]) for r in main]
    assert valid_id in ids
    assert len(main) == 1


def test_expired_does_not_crowd_valid_before_limit(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-limit"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    for i in range(8):
        _insert_temporal_assertion(
            conn,
            entity_id=entity_id,
            claim=f"expired-{i}",
            entrenchment_score=float(10 - i),
            valid_until=_40883_UNTIL,
        )
    valid_ids = [
        _insert_temporal_assertion(
            conn,
            entity_id=entity_id,
            claim=f"valid-{i}",
            entrenchment_score=0.01 * i,
            valid_until="2027-01-01T00:00:00+00:00",
        )
        for i in range(3)
    ]
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        card = get_entity_card(conn, entity_id=entity_id, top_k=2)
    top_ids = {a["id"] for a in card["top_k_assertions"]}
    assert top_ids <= set(valid_ids)
    assert card["assertion_counts"]["active"] == 3
    assert card["assertion_counts"]["inactive"] == 8


def test_malformed_bounds_inactive_not_current(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-malformed"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="reversed interval",
        valid_from="2027-01-01T00:00:00+00:00",
        valid_until="2026-01-01T00:00:00+00:00",
    )
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        card = get_entity_card(conn, entity_id=entity_id, top_k=7)
    assert card["top_k_assertions"] == []
    assert card["assertion_counts"]["active"] == 0
    assert card["assertion_counts"]["inactive"] == 1


def test_card_md_omits_expired_from_active_section(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-card-md"
    insert_entity(conn, entity_id=entity_id, entity_type="todo", name="Temporal MD")
    expired_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Expired for card-md",
        valid_until=_40883_UNTIL,
    )
    valid_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Valid for card-md",
        valid_until="2027-01-01T00:00:00+00:00",
    )
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        md = render_root_card_markdown(conn, entity_id=entity_id, top_k=5)
    assert "Expired for card-md" not in md
    assert "Valid for card-md" in md
    assert f"(id: {valid_id})" in md
    assert f"(id: {expired_id})" not in md


def test_sqlite_udf_matches_python(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    install_card_temporal_function(conn)
    row = conn.execute(
        "SELECT assertion_interval_contains(?, ?, ?)",
        ("2026-01-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00", _FROZEN_AS_OF),
    ).fetchone()
    assert row is not None and row[0] == 1
