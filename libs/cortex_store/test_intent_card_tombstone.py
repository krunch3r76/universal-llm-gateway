"""Required case 2 — Tombstone-only entity returns [summary] not [pointers].

Split from ``test_intent_card.py`` (SLOC waiver assertion 8521 on
``spec:cortex-v2.4``) by required-case grouping.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
from cortex_store.card import get_entity_card
from cortex_store.subgraph_template import render_root_card_markdown

_FROZEN_AS_OF = "2026-10-31T12:00:00+00:00"
_SUMMARY_FROM = "2020-04-04T00:00:00+00:00"
_SUMMARY_UNTIL = "2021-05-05T00:00:00+00:00"


def test_tombstone_collapses_to_summary_in_top_k(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="todo:tombstoned", entity_type="todo")
    # The summary lives in a superseded row (it was the supersede-input
    # before pointers replaced it).
    summary_id = insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim="archive summary 9999 — consolidated state of this todo",
        superseded_by=None,
    )
    # Active rows are pure pointers — entity is tombstone-only.
    pointer_id = insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )
    insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )

    # Mark the summary row superseded (FK-safe: point at a real assertion id).
    status_pred = "status(todo:tombstoned, ready, current)"
    conn.execute(
        "UPDATE assertions SET superseded_by = ?, valid_from = ?, valid_until = ?, "
        "predicate_form = ? WHERE id = ?",
        (pointer_id, _SUMMARY_FROM, _SUMMARY_UNTIL, status_pred, summary_id),
    )
    conn.commit()

    # Now active rows are only pointers; rebuild card.
    insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )

    with patch("cortex_store.card.card_as_of_utc", return_value=_FROZEN_AS_OF):
        card = get_entity_card(conn, entity_id="todo:tombstoned")
        markdown = render_root_card_markdown(conn, entity_id="todo:tombstoned")
    top_k = card["top_k_assertions"]
    assert len(top_k) == 1, "Tombstone-collapse must yield exactly the summary row"
    summary = top_k[0]
    assert summary["claim"].startswith("archive summary"), (
        f"Expected summary claim, got: {summary['claim']!r}"
    )
    assert "Compacted into" not in summary["claim"]
    assert summary["projection_role"] == "archive_summary"
    assert summary["valid_from"] == _SUMMARY_FROM
    assert summary["valid_until"] == _SUMMARY_UNTIL
    assert summary["superseded_by"] == pointer_id
    assert card["current_status"]["served"] is None
    assert card["current_status"]["withheld_count"] == 0
    assert card["predicate_summary"] == "tombstoned"
    assert "ready" not in card["predicate_summary"]
    assert "## Assertions (top 7)" in markdown
    assert "## Active Assertions" not in markdown
    assert "[archive summary]" in markdown
    claim_at = markdown.index(summary["claim"])
    assert "[archive summary]" in markdown[max(0, claim_at - 48) : claim_at]
    assert f"valid_from={_SUMMARY_FROM}" in markdown[claim_at : claim_at + 240]
    assert f"valid_until={_SUMMARY_UNTIL}" in markdown[claim_at : claim_at + 240]
