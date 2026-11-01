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
from cortex_store.subgraph_renderer import render_subgraph
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


def test_private_helper_installs_udf_on_fresh_connection(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    before = {str(row[0]) for row in conn.execute("PRAGMA function_list")}
    assert "assertion_interval_contains" not in before
    entity_id = "todo:temporal-helper-udf"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Visible to a fresh helper",
        valid_until="2027-01-01T00:00:00+00:00",
    )
    rows = _fetch_main_top_k(conn, entity_id=entity_id, top_k=7, as_of=_FROZEN_AS_OF)
    after = {str(row[0]) for row in conn.execute("PRAGMA function_list")}
    assert "assertion_interval_contains" in after
    assert [str(row["claim"]) for row in rows] == ["Visible to a fresh helper"]


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
    expired_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Expired readiness status",
        entrenchment_score=9.9,
        valid_until=_40883_UNTIL,
        predicate_form=status_pred,
    )
    stored_before = conn.execute(
        "SELECT claim, valid_from, valid_until, superseded_by, predicate_form, "
        "entrenchment_score, review_status FROM assertions WHERE id = ?",
        (expired_id,),
    ).fetchone()
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
    stored_after = conn.execute(
        "SELECT claim, valid_from, valid_until, superseded_by, predicate_form, "
        "entrenchment_score, review_status FROM assertions WHERE id = ?",
        (expired_id,),
    ).fetchone()
    assert tuple(stored_before) == tuple(stored_after)
    assert not status_rows
    assert card["current_status"]["served"] is None
    assert card["current_status"]["withheld_count"] == 0
    assert card["current_status"]["withheld_newer"] == []
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
    assert card["current_status"]["withheld_count"] == 0
    assert card["current_status"]["withheld_newer"] == []


def test_unflagged_expired_newer_status_cannot_replace_older_valid(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-status-unflagged"
    status_pred = f"status({entity_id}, employed, current)"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    older_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Older valid employment",
        predicate_form=status_pred,
        valid_until="2027-01-01T00:00:00+00:00",
    )
    conn.execute(
        "UPDATE assertions SET created_at = ? WHERE id = ?",
        ("2026-01-01T00:00:00+00:00", older_id),
    )
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "entrenchment_score, valid_from, valid_until, review_status, created_at, "
        "updated_at) VALUES (?, ?, 'believed', ?, 9.0, ?, ?, NULL, ?, ?)",
        (
            entity_id,
            "Newer unflagged expired employment",
            status_pred,
            "2026-01-01T00:00:00+00:00",
            _40883_UNTIL,
            "2026-10-31T00:00:00+00:00",
            "2026-10-31T00:00:00+00:00",
        ),
    )
    conn.commit()
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        card = get_entity_card(conn, entity_id=entity_id, top_k=7)
    served = card["current_status"]["served"]
    assert served is not None
    assert served["id"] == older_id
    assert served["claim"] == "Older valid employment"
    assert card["current_status"]["withheld_count"] == 0
    assert card["current_status"]["withheld_newer"] == []


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


def test_sparse_batch_shares_one_as_of_under_crossing_clock(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    root = "todo:temporal-sparse-root"
    neighbor = "todo:temporal-sparse-neighbor"
    for entity_id in (root, neighbor):
        insert_entity(conn, entity_id=entity_id, entity_type="todo")
        _insert_temporal_assertion(
            conn,
            entity_id=entity_id,
            claim=f"Expiring claim {entity_id}",
            valid_until="2026-11-01T00:00:00+00:00",
        )
    calls: list[str] = []

    def _as_of_sequence() -> str:
        if not calls:
            calls.append("2026-10-31T11:00:00+00:00")
        else:
            calls.append("2026-11-02T00:00:00+00:00")
        return calls[-1]

    kwargs = {
        "conn": conn,
        "visited_ids": [root, neighbor],
        "top_k_assertions": 7,
        "include_superseded": False,
        "root": root,
        "visited": {root: 0, neighbor: 1},
        "neighbor_fidelity": "depth_aware",
        "hub_rel_threshold": 20,
        "rel_counts": {root: 0, neighbor: 0},
    }
    with patch(
        "cortex_store.subgraph_cards.card_as_of_utc", side_effect=_as_of_sequence
    ):
        first = build_cards(**kwargs)
    assert calls == ["2026-10-31T11:00:00+00:00"]
    assert first[root]["assertion_counts"]["active"] == 1
    assert first[neighbor]["active_assertion_count"] == 1
    assert first[neighbor]["top_k_assertions"] == []
    with patch(
        "cortex_store.subgraph_cards.card_as_of_utc", side_effect=_as_of_sequence
    ):
        second = build_cards(**kwargs)
    assert calls == [
        "2026-10-31T11:00:00+00:00",
        "2026-11-02T00:00:00+00:00",
    ]
    assert second[root]["assertion_counts"]["active"] == 0
    assert second[neighbor]["active_assertion_count"] == 0


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
    expired_from = "2020-01-01T00:00:00+00:00"
    expired_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Expired unsuperseded row",
        entrenchment_score=5.0,
        valid_from=expired_from,
        valid_until=_40883_UNTIL,
    )
    archive_like_id = _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="archive summary lookalike historical row",
        entrenchment_score=4.0,
        valid_from=expired_from,
        valid_until=_40883_UNTIL,
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
    assert roles[expired_id] == "history"
    assert roles[archive_like_id] == "history"
    assert roles[superseded_id] == "history"
    hist = next(a for a in top if int(a["id"]) == superseded_id)
    assert hist.get("superseded_by") == live_id
    expired = next(a for a in top if int(a["id"]) == expired_id)
    assert expired["valid_from"] == expired_from
    assert expired["valid_until"] == _40883_UNTIL
    assert expired["superseded_by"] is None
    lookalike = next(a for a in top if int(a["id"]) == archive_like_id)
    assert lookalike["projection_role"] == "history"
    assert lookalike["valid_from"] == expired_from
    assert lookalike["valid_until"] == _40883_UNTIL


def _relate(conn: sqlite3.Connection, src: str, tgt: str) -> None:
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES (?, ?, 'depends_on', 1)",
        (src, tgt),
    )
    conn.commit()


def _assert_markdown_matches_role(
    markdown: str,
    row: dict[str, object],
    *,
    label: str,
) -> None:
    claim = str(row["claim"])
    idx = markdown.index(claim)
    assert label in markdown[max(0, idx - 48) : idx]
    assert f"valid_from={row['valid_from']}" in markdown[idx : idx + 240]
    assert f"valid_until={row['valid_until']}" in markdown[idx : idx + 240]


def test_historical_root_markdown_agrees_with_json(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-md-history"
    valid_from = "2020-01-01T00:00:00+00:00"
    valid_until = "2021-06-01T00:00:00+00:00"
    insert_entity(conn, entity_id=entity_id, entity_type="todo", name="History MD")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="archive summary lookalike historical row",
        entrenchment_score=3.0,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        rendered = render_subgraph(
            conn,
            entity_id,
            hops=1,
            top_k_assertions=3,
            include_superseded=True,
            neighbor_fidelity="full",
        )
    md = rendered.rendered
    root = next(e for e in rendered.entities if e.entity_id == entity_id)
    row = root.card["top_k_assertions"][0]
    assert row["projection_role"] == "history"
    assert row["valid_from"] == valid_from
    assert row["valid_until"] == valid_until
    assert "## Assertions (top 3)" in md
    assert "## Active Assertions" not in md
    assert "[archive summary]" not in md
    _assert_markdown_matches_role(md, row, label="[history]")


def test_current_root_keeps_active_assertions_heading(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:temporal-md-current"
    insert_entity(conn, entity_id=entity_id, entity_type="todo", name="Current MD")
    _insert_temporal_assertion(
        conn,
        entity_id=entity_id,
        claim="Still current claim",
        valid_until="2027-01-01T00:00:00+00:00",
    )
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        rendered = render_subgraph(
            conn,
            entity_id,
            hops=1,
            top_k_assertions=3,
            include_superseded=False,
            neighbor_fidelity="full",
        )
    assert "## Active Assertions (top 3)" in rendered.rendered
    assert "## Assertions (top 3)" not in rendered.rendered
    assert "[history]" not in rendered.rendered
    assert "[archive summary]" not in rendered.rendered


def test_neighbor_blocks_label_history_and_archive_bounds(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    valid_from = "2020-02-02T00:00:00+00:00"
    valid_until = "2021-03-03T00:00:00+00:00"
    root = "todo:temporal-neighbor-root"
    history_id = "todo:temporal-neighbor-history"
    archive_id = "todo:temporal-neighbor-archive"
    insert_entity(conn, entity_id=root, entity_type="todo", name="Neighbor Root")
    insert_entity(
        conn, entity_id=history_id, entity_type="todo", name="History Neighbor"
    )
    insert_entity(
        conn, entity_id=archive_id, entity_type="todo", name="Archive Neighbor"
    )
    _insert_temporal_assertion(
        conn,
        entity_id=root,
        claim="Root stays current",
        valid_until="2027-01-01T00:00:00+00:00",
    )
    _insert_temporal_assertion(
        conn,
        entity_id=history_id,
        claim="Neighbor historical claim",
        entrenchment_score=2.0,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    summary_id = _insert_temporal_assertion(
        conn,
        entity_id=archive_id,
        claim="archive summary neighbor consolidation",
        entrenchment_score=1.0,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    pointer_id = _insert_temporal_assertion(
        conn,
        entity_id=archive_id,
        claim=f"Compacted into archive summary {summary_id}",
    )
    conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?",
        (pointer_id, summary_id),
    )
    conn.commit()
    _insert_temporal_assertion(
        conn,
        entity_id=archive_id,
        claim=f"Compacted into archive summary {summary_id}",
    )
    _relate(conn, root, history_id)
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        full_history = render_subgraph(
            conn,
            root,
            hops=1,
            top_k_assertions=3,
            include_superseded=True,
            neighbor_fidelity="full",
        )
        hub_history = render_subgraph(
            conn,
            root,
            hops=1,
            top_k_assertions=3,
            include_superseded=True,
            neighbor_fidelity="depth_aware",
            hub_rel_threshold=1,
        )
    history_card = next(
        e.card for e in full_history.entities if e.entity_id == history_id
    )
    history_row = history_card["top_k_assertions"][0]
    assert history_row["projection_role"] == "history"
    assert history_row["valid_from"] == valid_from
    assert history_row["valid_until"] == valid_until
    _assert_markdown_matches_role(full_history.rendered, history_row, label="[history]")
    _assert_markdown_matches_role(hub_history.rendered, history_row, label="[history]")
    assert "Neighbor historical claim" in hub_history.rendered

    _relate(conn, root, archive_id)
    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        full_archive = render_subgraph(
            conn,
            root,
            hops=1,
            top_k_assertions=3,
            include_superseded=False,
            neighbor_fidelity="full",
        )
        hub_archive = render_subgraph(
            conn,
            root,
            hops=1,
            top_k_assertions=3,
            include_superseded=False,
            neighbor_fidelity="depth_aware",
            hub_rel_threshold=1,
        )
    archive_card = next(
        e.card for e in full_archive.entities if e.entity_id == archive_id
    )
    archive_row = archive_card["top_k_assertions"][0]
    assert archive_row["projection_role"] == "archive_summary"
    assert archive_row["valid_from"] == valid_from
    assert archive_row["valid_until"] == valid_until
    assert archive_row["superseded_by"] == pointer_id
    _assert_markdown_matches_role(
        full_archive.rendered, archive_row, label="[archive summary]"
    )
    _assert_markdown_matches_role(
        hub_archive.rendered, archive_row, label="[archive summary]"
    )


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
