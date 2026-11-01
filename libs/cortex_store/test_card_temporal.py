"""Card read-path temporal eligibility — current projection before LIMIT."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.card import (
    _fetch_main_top_k,
    _fetch_status_current_rows,
    get_entity_card,
)
from cortex_store.card_temporal import (
    assertion_interval_contains,
    install_card_temporal_function,
)
from cortex_store.subgraph_cards import build_cards
from cortex_store.subgraph_template import (
    _render_full_entity_block,
    render_root_card_markdown,
)

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
        ("", None, _FROZEN_AS_OF, False),
        ("   ", None, _FROZEN_AS_OF, False),
        (None, "", _FROZEN_AS_OF, False),
        (None, "9999-12-31T23:59:59-23:59", _FROZEN_AS_OF, False),
        ("2026-10-31T12:00:00", None, _FROZEN_AS_OF, True),
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
        main = _fetch_main_top_k(
            conn, entity_id=entity_id, top_k=7, as_of=_FROZEN_AS_OF
        )
    ids = [int(r["id"]) for r in main]
    assert valid_id in ids
    assert len(main) == 1


def test_expired_does_not_crowd_valid_before_limit(
    migrated_conn: sqlite3.Connection,
) -> None:
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


def test_malformed_bounds_inactive_not_current(
    migrated_conn: sqlite3.Connection,
) -> None:
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


def test_card_md_omits_expired_from_active_section(
    migrated_conn: sqlite3.Connection,
) -> None:
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


def test_unsupported_bound_type_inactive(migrated_conn: sqlite3.Connection) -> None:
    assert assertion_interval_contains(42, None, _FROZEN_AS_OF) is False


def test_sqlite_udf_extreme_date_no_exception(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    install_card_temporal_function(conn)
    row = conn.execute(
        "SELECT assertion_interval_contains(?, ?, ?)",
        (None, "9999-12-31T23:59:59-23:59", _FROZEN_AS_OF),
    ).fetchone()
    assert row is not None and row[0] == 0


def test_sqlite_udf_function_list_deterministic(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    install_card_temporal_function(conn)
    rows = conn.execute("PRAGMA function_list").fetchall()
    names = {str(r[0]): r for r in rows}
    row = names["assertion_interval_contains"]
    assert row is not None
    # Final column carries SQLITE_DETERMINISTIC (2048) for Python UDFs.
    assert int(row[5]) & 2048


def test_expired_status_predicate_absent_from_current_surfaces(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-status-expired"
    status_pred = f"status({entity_id}, ready, current)"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Expired readiness status",
        entrenchment_score=9.9,
        valid_until=_40883_UNTIL,
        predicate_form=status_pred,
    )
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Ordinary valid claim",
        entrenchment_score=0.1,
        valid_until="2027-01-01T00:00:00+00:00",
    )
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        card = get_entity_card(conn, entity_id=entity_id, top_k=7)
        status_rows = _fetch_status_current_rows(
            conn, entity_id=entity_id, as_of=_FROZEN_AS_OF
        )
    assert not status_rows
    assert card["current_status"]["served"] is None
    assert card["current_status"]["withheld_count"] == 0
    assert "ready" not in (card.get("predicate_summary") or "").lower()
    assert all("Expired readiness" not in a["claim"] for a in card["top_k_assertions"])


def test_expired_newer_status_does_not_override_older_valid(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-status-pin"
    status_pred = f"status({entity_id}, employed, current)"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Current employment status",
        predicate_form=status_pred,
        valid_until="2027-01-01T00:00:00+00:00",
    )
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "entrenchment_score, valid_from, valid_until, review_status, created_at, updated_at) "
        "VALUES (?, ?, 'believed', ?, 1.0, ?, ?, 'flagged', ?, ?)",
        (
            entity_id,
            "Newer expired flagged status",
            status_pred,
            _FROZEN_AS_OF,
            _40883_UNTIL,
            _FROZEN_AS_OF,
            _FROZEN_AS_OF,
        ),
    )
    conn.commit()
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        card = get_entity_card(conn, entity_id=entity_id, top_k=7)
    served = card["current_status"]["served"]
    assert served is not None
    assert served["claim"] == "Current employment status"
    assert card["current_status"]["withheld_count"] >= 0


def test_explicit_empty_as_of_not_replaced_with_now(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-empty-as-of"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Future-only",
        valid_from="2027-01-01T00:00:00+00:00",
    )
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        card = get_entity_card(conn, entity_id=entity_id, top_k=7, as_of="")
    assert card["top_k_assertions"] == []
    assert card["assertion_counts"]["active"] == 0


def test_build_cards_single_as_of_across_batch(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-build-cards-as-of"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Valid at first as_of",
        valid_until="2026-11-01T00:00:00+00:00",
    )
    calls: list[str] = []

    def _as_of_sequence() -> str:
        if not calls:
            calls.append("2026-10-31T11:00:00+00:00")
        else:
            calls.append("2026-11-02T00:00:00+00:00")
        return calls[-1]

    with patch(
        "cortex_store.subgraph_cards.card_as_of_utc", side_effect=_as_of_sequence
    ):
        cards_before = build_cards(
            conn=conn,
            visited_ids=[entity_id],
            top_k_assertions=7,
            include_superseded=False,
        )
    assert cards_before[entity_id]["assertion_counts"]["active"] == 1

    with patch(
        "cortex_store.subgraph_cards.card_as_of_utc", side_effect=_as_of_sequence
    ):
        cards_after = build_cards(
            conn=conn,
            visited_ids=[entity_id],
            top_k_assertions=7,
            include_superseded=False,
        )
    assert len(calls) == 2
    assert cards_after[entity_id]["assertion_counts"]["active"] == 0


def test_historical_override_marks_history_not_current(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-hist-override"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    live_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Still active unsuperseded",
        entrenchment_score=0.1,
        valid_until="2027-01-01T00:00:00+00:00",
    )
    superseded_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Superseded row",
        entrenchment_score=9.0,
    )
    conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?",
        (live_id, superseded_id),
    )
    conn.commit()
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        cards = build_cards(
            conn=conn,
            visited_ids=[entity_id],
            top_k_assertions=7,
            include_superseded=True,
        )
    top = cards[entity_id]["top_k_assertions"]
    roles = {int(a["id"]): a.get("projection_role") for a in top}
    assert roles[live_id] == "history"
    assert roles[superseded_id] == "history"
    hist = next(a for a in top if int(a["id"]) == superseded_id)
    assert hist.get("superseded_by") == live_id


def test_card_md_labels_history_rows(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-md-history"
    insert_entity(conn, entity_id=entity_id, entity_type="todo", name="History MD")
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        cards = build_cards(
            conn=conn,
            visited_ids=[entity_id],
            top_k_assertions=3,
            include_superseded=True,
        )
        cards[entity_id]["top_k_assertions"] = [
            {
                "id": 1,
                "claim": "Historical claim",
                "confidence": "believed",
                "projection_role": "history",
                "valid_from": "2020-01-01T00:00:00+00:00",
                "valid_until": "2021-01-01T00:00:00+00:00",
            }
        ]
        block = _render_full_entity_block(
            entity_id,
            cards[entity_id],
            {},
            {},
            1,
            [],
        )
    assert "[history]" in block
    assert "valid_until=" in block
    md = render_root_card_markdown(conn, entity_id=entity_id, top_k=3)
    assert "## Active Assertions" in md or "## Assertions" in md


def test_malformed_bounds_retained_inactive_no_query_exception(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-hist-read"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    bad_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="bad bounds row",
        valid_from="2027-01-01T00:00:00+00:00",
        valid_until="2026-01-01T00:00:00+00:00",
    )
    install_card_temporal_function(conn)
    row = conn.execute(
        "SELECT id FROM assertions WHERE entity_id = ? AND superseded_by IS NULL "
        "AND assertion_interval_contains(valid_from, valid_until, ?)",
        (entity_id, _FROZEN_AS_OF),
    ).fetchone()
    assert row is None
    hist = conn.execute(
        "SELECT id FROM assertions WHERE entity_id = ? AND id = ?",
        (entity_id, bad_id),
    ).fetchone()
    assert hist is not None
