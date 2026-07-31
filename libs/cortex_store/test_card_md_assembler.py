"""card-md root-only markdown assembler — entity_get(intent='card-md')."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
from cortex_store.dispatch_ops.ops_entities import _op_entity_get
from cortex_store.subgraph_template import render_root_card_markdown


def test_render_root_card_markdown_root_only_shape(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:card-md-shape"
    insert_entity(
        conn,
        entity_id=entity_id,
        entity_type="todo",
        name="Card MD Shape",
        description="Entity for card-md assembler coverage.",
    )
    assn_id = insert_assertion(
        conn,
        entity_id=entity_id,
        claim="Active assertion claim for card-md rendering.",
        confidence="believed",
    )

    md = render_root_card_markdown(conn, entity_id=entity_id, top_k=3)

    assert isinstance(md, str)
    assert md.startswith("# Card MD Shape")
    assert "## State signals" not in md  # empty predicate_summary omitted
    assert "## Active Assertions (top 3)" in md
    assert f"(id: {assn_id})" in md
    assert "## Sections" in md
    assert "Audit trail (active): 1" in md
    assert "## Related Entities" not in md


def test_op_entity_get_card_md_returns_markdown_string(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:card-md-dispatch"
    insert_entity(conn, entity_id=entity_id, entity_type="todo", name="Dispatch Card MD")
    insert_assertion(conn, entity_id=entity_id, claim="Dispatch path assertion.")

    class _Ctx:
        def __enter__(self) -> sqlite3.Connection:
            return conn

        def __exit__(self, *a: object) -> None:
            return None

    with patch("cortex_store.dispatch_ops.ops_entities.cortex_conn", _Ctx):
        result = _op_entity_get(entity_id=entity_id, intent="card-md", top_k=5)

    assert isinstance(result, str)
    assert "## Active Assertions (top 5)" in result
    assert "(id:" in result
    assert "## Sections" in result
    assert "## Related Entities" not in result
