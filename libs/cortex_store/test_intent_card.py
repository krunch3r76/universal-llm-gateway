"""Layer 2 integration tests for Cortex v2.4 intent=card (Slice 2).

Required cases (todo:cortex-v24-slice2-adapters):
  1. predicate_summary is never None
  2. Tombstone-only entity returns [summary] not [pointers] in top_k_assertions
  3. top_k=0 rejected by dispatch op
  4. fetch_plan_row_volume for card < full on same entity (§7.7 anti-load-and-trim)
  5. intent in {cluster, impact} returns 501-style hint at dispatch surface
  6. Each adapter type produces expected section_manifest ids
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

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
from cortex_store.entity_crud import get_entity_impl

# ---------------------------------------------------------------------------
# Schema fixture — in-memory SQLite mirroring the columns the impl reads.
# ---------------------------------------------------------------------------

# §6.4 stance — per-adapter section-id contract is asserted explicitly via
# each adapter's ``expected_section_ids`` ClassVar (see
# decision:cortex-v24-card-section-uniformity). Lists currently match across
# adapters, but the binding is per-type so future divergence is a one-line
# edit on the diverging adapter, not a framework change.


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT,
            workflow_state TEXT,
            attributes TEXT,
            aliases TEXT,
            notes TEXT,
            source_uri TEXT,
            content_hash TEXT,
            retention_policy TEXT,
            retention_ttl_days INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            confidence TEXT NOT NULL,
            evidence TEXT,
            evidence_uris TEXT,
            seeded_by TEXT,
            chunk_id INTEGER,
            derivation_type TEXT,
            reasoning_summary TEXT,
            is_atomic INTEGER DEFAULT 1,
            is_decontextualized INTEGER DEFAULT 1,
            observed_at TEXT,
            valid_from TEXT,
            valid_until TEXT,
            confidence_score REAL,
            superseded_by INTEGER,
            review_status TEXT,
            reviewer TEXT,
            reviewed_at TEXT,
            review_notes TEXT,
            resolution_status TEXT,
            fulfillment_assertion_id INTEGER,
            quality_score REAL,
            prospective_summary TEXT,
            events_json TEXT,
            artifact_uri TEXT,
            artifact_storage TEXT,
            entrenchment_score REAL,
            created_at TEXT
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            type TEXT NOT NULL,
            role TEXT,
            strength REAL,
            evidence TEXT,
            chunk_id INTEGER,
            valid_from TEXT,
            valid_until TEXT,
            source_uri TEXT,
            session_id TEXT,
            agent TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE relationship_types (
            type TEXT PRIMARY KEY,
            description TEXT
        );
        CREATE TABLE session_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_node TEXT,
            to_node TEXT,
            edge_type TEXT,
            valid_until TEXT,
            created_at TEXT
        );
        CREATE TABLE entity_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT,
            agent TEXT,
            operation TEXT,
            source TEXT,
            session_id TEXT
        );
        """
    )
    return conn


def _insert_entity(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    name: str = "Test entity",
    description: str = "Test description.",
    status: str = "confirmed",
    workflow_state: str | None = None,
    attributes: str | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO entities (id, type, name, description, status, workflow_state, "
        "attributes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            entity_type,
            name,
            description,
            status,
            workflow_state,
            attributes,
            now,
            now,
        ),
    )
    conn.commit()


def _insert_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
    confidence: str = "believed",
    superseded_by: int | None = None,
) -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, superseded_by, "
        "created_at) VALUES (?, ?, ?, ?, ?)",
        (entity_id, claim, confidence, superseded_by, now),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


# ---------------------------------------------------------------------------
# Test 1 — predicate_summary is never None
# ---------------------------------------------------------------------------


def test_predicate_summary_never_none_on_empty_entity() -> None:
    """Bare entity (no assertions, no edges) — slot is "" (empty string), not None."""
    conn = _make_conn()
    _insert_entity(conn, entity_id="todo:bare", entity_type="todo")
    card = get_entity_card(conn, entity_id="todo:bare")
    assert card["predicate_summary"] is not None
    assert isinstance(card["predicate_summary"], str)


def test_predicate_summary_never_none_with_relationships() -> None:
    conn = _make_conn()
    _insert_entity(conn, entity_id="todo:linked", entity_type="todo")
    _insert_entity(conn, entity_id="person:alice", entity_type="person")
    conn.execute(
        "INSERT INTO relationship_types (type, description) VALUES "
        "('mentions', 'mentions')"
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active, created_at) "
        "VALUES (?, ?, 'mentions', 1, ?)",
        ("todo:linked", "person:alice", datetime.now(UTC).isoformat()),
    )
    conn.commit()

    card = get_entity_card(conn, entity_id="todo:linked")
    assert card["predicate_summary"] is not None
    assert "mentions" in card["predicate_summary"]


# ---------------------------------------------------------------------------
# Test 2 — Tombstone-only entity → [summary] not [pointers] in top_k_assertions
# ---------------------------------------------------------------------------


def test_tombstone_collapses_to_summary_in_top_k() -> None:
    conn = _make_conn()
    _insert_entity(conn, entity_id="todo:tombstoned", entity_type="todo")
    # The summary lives in a superseded row (it was the supersede-input
    # before pointers replaced it).
    summary_id = _insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim="archive summary 9999 — consolidated state of this todo",
        superseded_by=None,
    )
    # Active rows are pure pointers — entity is tombstone-only.
    _insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )
    _insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )

    # Mark the summary row superseded (as in real tombstoning).
    conn.execute("UPDATE assertions SET superseded_by = -1 WHERE id = ?", (summary_id,))
    conn.commit()

    # Now active rows are only pointers; rebuild card.
    _insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )

    card = get_entity_card(conn, entity_id="todo:tombstoned")
    top_k = card["top_k_assertions"]
    assert len(top_k) == 1, "Tombstone-collapse must yield exactly the summary row"
    assert top_k[0]["claim"].startswith("archive summary"), (
        f"Expected summary claim, got: {top_k[0]['claim']!r}"
    )
    assert "Compacted into" not in top_k[0]["claim"]
    # predicate_summary navigation hint
    assert card["predicate_summary"] is not None


# ---------------------------------------------------------------------------
# Test 3 — top_k=0 rejected by dispatch op
# ---------------------------------------------------------------------------


def test_top_k_zero_rejected_at_dispatch() -> None:
    result = _op_entity_get(entity_id="todo:anything", intent="card", top_k=0)
    assert "error" in result
    assert "top_k" in result["error"]


def test_top_k_negative_rejected_at_dispatch() -> None:
    result = _op_entity_get(entity_id="todo:anything", intent="card", top_k=-3)
    assert "error" in result


def test_top_k_above_cap_rejected_at_dispatch() -> None:
    result = _op_entity_get(entity_id="todo:anything", intent="card", top_k=51)
    assert "error" in result


# ---------------------------------------------------------------------------
# Test 4 — fetch_plan_row_volume for card < full on same entity
# ---------------------------------------------------------------------------


def test_card_fetch_plan_row_volume_smaller_than_full() -> None:
    """§7.7 anti-load-and-trim: card mode must materialize fewer rows than full.

    Seed >>top_k assertions so the difference is unambiguous: the full path
    materializes every assertion + every relationship + every edge; card
    materializes only the top_k assertions plus aggregate counts.
    """
    conn = _make_conn()
    _insert_entity(conn, entity_id="todo:bulk", entity_type="todo")
    for i in range(40):
        _insert_assertion(
            conn,
            entity_id="todo:bulk",
            claim=f"Operative claim #{i:02d} for the bulk-load test entity.",
        )
    # Some superseded rows too, to widen the gap.
    superseder_id = _insert_assertion(
        conn,
        entity_id="todo:bulk",
        claim="Superseder",
    )
    for i in range(15):
        _insert_assertion(
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
    full_min_rows = 1 + len(full["assertions"])  # entity + assertion stream
    assert full_min_rows >= 50

    assert card_rows < full_min_rows, (
        f"card fetch_plan_row_volume={card_rows} should be much less than the "
        f"full path's assertion+entity row count={full_min_rows} "
        f"(§6.2 anti-load-and-trim)."
    )
    # Hard upper bound: card cannot exceed top_k + small fixed overhead.
    assert card_rows <= 7 + 8, (
        f"card fetch_plan_row_volume={card_rows} exceeds top_k + overhead bound"
    )


# ---------------------------------------------------------------------------
# Test 5 — intent in {cluster, impact} returns 501-style hint at dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent", ["cluster", "impact"])
def test_deferred_intents_rejected_at_dispatch(intent: str) -> None:
    result = _op_entity_get(entity_id="todo:anything", intent=intent)
    assert "error" in result
    assert "reserved" in result["error"]
    assert result.get("supported_intents") == ["full", "card"]


def test_unknown_intent_rejected_at_dispatch() -> None:
    result = _op_entity_get(entity_id="todo:anything", intent="bogus")
    assert "error" in result
    assert "Unknown intent" in result["error"]


# ---------------------------------------------------------------------------
# Test 6 — Each adapter type produces expected section_manifest ids
# ---------------------------------------------------------------------------


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
    """Each registered type resolves to its adapter; unknown types → DefaultAdapter.

    Per §6.4: each adapter's ``expected_section_ids`` ClassVar is the
    binding contract. The test asserts the emitted section ids exactly
    match that per-adapter declaration — current equality across types is
    observed, not asserted as a framework constraint.
    """
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
    conn = _make_conn()
    _insert_entity(
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


# ---------------------------------------------------------------------------
# Bonus: dispatch op happy-path uses the real cortex_conn() — patch for memory.
# ---------------------------------------------------------------------------


def test_dispatch_op_card_happy_path() -> None:
    """Dispatch op end-to-end with intent=card returns a card-shaped payload."""
    conn = _make_conn()
    _insert_entity(conn, entity_id="todo:dispatch", entity_type="todo")

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
