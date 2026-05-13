"""Required case 4 — fetch_plan_row_volume for card < full on same entity.

§7.7 anti-load-and-trim: card mode must materialize fewer rows than full.

Split from ``test_intent_card.py`` (SLOC waiver assertion 8521 on
``spec:cortex-v2.4``) by required-case grouping.
"""

from __future__ import annotations

from cortex_store._intent_card_test_fixtures import (
    insert_assertion,
    insert_entity,
    make_conn,
)
from cortex_store.card import get_entity_card
from cortex_store.entity_read import get_entity_impl


def test_card_fetch_plan_row_volume_smaller_than_full() -> None:
    """Seed >>top_k assertions so the difference is unambiguous: the full path
    materializes every assertion + every relationship + every edge; card
    materializes only the top_k assertions plus aggregate counts.
    """
    conn = make_conn()
    insert_entity(conn, entity_id="todo:bulk", entity_type="todo")
    for i in range(40):
        insert_assertion(
            conn,
            entity_id="todo:bulk",
            claim=f"Operative claim #{i:02d} for the bulk-load test entity.",
        )
    superseder_id = insert_assertion(
        conn,
        entity_id="todo:bulk",
        claim="Superseder",
    )
    for i in range(15):
        insert_assertion(
            conn,
            entity_id="todo:bulk",
            claim=f"Older claim #{i}",
            superseded_by=superseder_id,
        )

    card = get_entity_card(conn, entity_id="todo:bulk", top_k=7, debug=True)
    assert card["debug"] is not None
    card_rows = int(card["debug"]["fetch_plan_row_volume"])

    # The full impl doesn't expose row volume; compute the lower bound directly:
    # it touches every active + superseded assertion, plus the entity row,
    # plus relationship + edge rows. With 56 assertions seeded, the full
    # path materializes ≥56 rows; card budget for top_k=7 is bounded by
    # 1 entity + 7 assertions + 1 count-row + edge-aggregate rows ≪ 56.
    full = get_entity_impl(conn, entity_id="todo:bulk")
    full_min_rows = 1 + len(full["assertions"])
    assert full_min_rows >= 50

    assert card_rows < full_min_rows, (
        f"card fetch_plan_row_volume={card_rows} should be much less than the "
        f"full path's assertion+entity row count={full_min_rows} "
        f"(§6.2 anti-load-and-trim)."
    )
    assert card_rows <= 7 + 8, (
        f"card fetch_plan_row_volume={card_rows} exceeds top_k + overhead bound"
    )
