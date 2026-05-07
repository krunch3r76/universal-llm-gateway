"""Required case 6 — Each adapter type produces expected section_manifest ids.

Plus dispatch-op happy-path end-to-end and todo-specific status_summary
sanity check (originally bonus tests in the unified file). All
adapter-touching cases live here together.

Split from ``test_intent_card.py`` (SLOC waiver assertion 8521 on
``spec:cortex-v2.4``) by required-case grouping.

§6.4 stance — per-adapter section-id contract is asserted explicitly via
each adapter's ``expected_section_ids`` ClassVar (see
``decision:cortex-v24-card-section-uniformity``). Lists currently match
across adapters, but the binding is per-type so future divergence is a
one-line edit on the diverging adapter, not a framework change.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_entity, make_conn
from cortex_store.card import get_entity_card
from cortex_store.card_adapters import (
    CaseAdapter,
    DecisionAdapter,
    DefaultAdapter,
    DocumentAdapter,
    PersonAdapter,
    ServiceAdapter,
    TodoAdapter,
    get_adapter,
)
from cortex_store.dispatch_ops.ops_entities import _op_entity_get


@pytest.mark.parametrize(
    ("entity_type", "adapter_cls"),
    [
        ("todo", TodoAdapter),
        ("decision", DecisionAdapter),
        ("document", DocumentAdapter),
        ("service", ServiceAdapter),
        ("case", CaseAdapter),
        ("person", PersonAdapter),
        ("unknown_type_x", DefaultAdapter),
    ],
)
def test_adapter_dispatch_and_section_ids(entity_type: str, adapter_cls: type) -> None:
    """Each registered type resolves to its adapter; unknown types → DefaultAdapter."""
    adapter = get_adapter(entity_type)
    assert isinstance(adapter, adapter_cls)

    sections = adapter.sections(
        {"id": f"{entity_type}:x", "type": entity_type},
        {
            "active_n": 3,
            "superseded_n": 1,
            "rel_total": 2,
            "archives_to_count": 0,
            "edges_n": 4,
        },
    )
    section_ids = tuple(s.id for s in sections)
    assert section_ids == adapter_cls.expected_section_ids
    assert all(s.label for s in sections)


def test_adapter_labels_differ_per_type() -> None:
    """Sanity: adapters differentiate via labels (transport stays uniform via ids)."""
    todo_sections = TodoAdapter().sections(
        {},
        {
            "active_n": 0,
            "superseded_n": 0,
            "rel_total": 0,
            "archives_to_count": 0,
            "edges_n": 0,
        },
    )
    default_sections = DefaultAdapter().sections(
        {},
        {
            "active_n": 0,
            "superseded_n": 0,
            "rel_total": 0,
            "archives_to_count": 0,
            "edges_n": 0,
        },
    )
    todo_label = next(s.label for s in todo_sections if s.id == "assertions")
    default_label = next(s.label for s in default_sections if s.id == "assertions")
    assert todo_label != default_label


def test_card_uses_type_specific_status_summary_for_todo() -> None:
    """End-to-end: a todo card carries todo-specific status_summary keys."""
    conn = make_conn()
    insert_entity(
        conn,
        entity_id="todo:abc",
        entity_type="todo",
        workflow_state="open",
        attributes='{"priority": "high", "domain": "cortex"}',
    )
    card = get_entity_card(conn, entity_id="todo:abc")
    status = card["status_summary"]
    assert status is not None
    assert status.get("priority") == "high"
    assert status.get("domain") == "cortex"
    assert status.get("workflow_state") == "open"


def test_dispatch_op_card_happy_path() -> None:
    """Dispatch op end-to-end with intent=card returns a card-shaped payload."""
    conn = make_conn()
    insert_entity(conn, entity_id="todo:dispatch", entity_type="todo")

    class _Ctx:
        def __enter__(self) -> sqlite3.Connection:
            return conn

        def __exit__(self, *a: object) -> None:
            return None

    with patch("cortex_store.dispatch_ops.ops_entities.cortex_conn", _Ctx):
        result = _op_entity_get(entity_id="todo:dispatch", intent="card", debug=True)
    assert result["intent"] == "card"
    assert result["id"] == "todo:dispatch"
    assert result["debug"] is not None
    assert result["debug"]["fetch_plan_row_volume"] >= 1
