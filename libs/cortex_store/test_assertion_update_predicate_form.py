"""Integration tests for predicate_form writeback via assertion_update.

Acceptance criteria §5 from todo:cortex-api-assertion-update-predicate-form:
  - NULL→value (set from absent)
  - value→value (overwrite)
  - explicit null (clear)
  - validation: empty string rejected (422)
  - validation: >2000 chars rejected (422)

Tests use an in-memory SQLite DB and patch cortex_conn in the route module,
matching the fixture pattern in test_bulk_write_surface.py and
_intent_card_test_fixtures.py.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from cortex_store.routes.assertions import _update_assertion_impl

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_ASSERTIONS_DDL = """
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    confidence TEXT NOT NULL,
    confidence_score REAL,
    evidence TEXT,
    evidence_uris TEXT,
    seeded_by TEXT,
    derivation_type TEXT,
    chunk_id INTEGER,
    reasoning_summary TEXT,
    is_atomic INTEGER DEFAULT 1,
    is_decontextualized INTEGER DEFAULT 1,
    observed_at TEXT,
    valid_from TEXT,
    valid_until TEXT,
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
    artifact_storage TEXT DEFAULT 'inline',
    entrenchment_score REAL,
    predicate_form TEXT,
    raw_predicate_form TEXT,
    normalization_decision TEXT,
    candidate_set_fingerprint TEXT,
    normalizer_version TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_ASSERTIONS_DDL)
    # Phase B Q5.4 always-re-normalize calls DBEntityResolver(conn) which
    # reads `entities`; the table must exist even when these tests don't
    # exercise Class 2 entity rewriting.
    conn.executescript(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT NOT NULL);"
    )
    return conn


def _insert_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str = "test:entity",
    claim: str = "Test claim.",
    confidence: str = "believed",
    predicate_form: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form)"
        " VALUES (?, ?, ?, ?)",
        (entity_id, claim, confidence, predicate_form),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _get_predicate_form(conn: sqlite3.Connection, assertion_id: int) -> str | None:
    row = conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = ?", (assertion_id,)
    ).fetchone()
    return dict(row)["predicate_form"] if row else None


def _patch(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(
        "cortex_store.routes.assertions._update.cortex_conn", lambda: conn
    )


# ---------------------------------------------------------------------------
# NULL → value (set from absent)
# ---------------------------------------------------------------------------


def test_predicate_form_null_to_value(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form=None)

    # Already-canonical input — Q5.4 always-re-normalize must be idempotent.
    result = _update_assertion_impl(aid, {"predicate_form": "role(x, y, z)"})

    assert result["predicate_form"] == "role(x, y, z)"
    assert _get_predicate_form(conn, aid) == "role(x, y, z)"


# ---------------------------------------------------------------------------
# value → value (overwrite existing)
# ---------------------------------------------------------------------------


def test_predicate_form_value_to_value(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="role(old, x, y)")

    result = _update_assertion_impl(aid, {"predicate_form": "role(new, x, y)"})

    assert result["predicate_form"] == "role(new, x, y)"
    assert _get_predicate_form(conn, aid) == "role(new, x, y)"


# ---------------------------------------------------------------------------
# value → NULL (clear via explicit null in dict body)
# ---------------------------------------------------------------------------


def test_predicate_form_clear_via_explicit_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="role(to_be_cleared, x, y)")

    # Explicit null in dict → clearing intent (no normalize fires for null)
    result = _update_assertion_impl(aid, {"predicate_form": None})

    assert result["predicate_form"] is None
    assert _get_predicate_form(conn, aid) is None


# ---------------------------------------------------------------------------
# omit predicate_form → no change (other field updated, predicate_form untouched)
# ---------------------------------------------------------------------------


def test_predicate_form_untouched_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="role(preserved, x, y)")

    result = _update_assertion_impl(aid, {"review_status": "staged"})

    assert result["predicate_form"] == "role(preserved, x, y)"
    assert _get_predicate_form(conn, aid) == "role(preserved, x, y)"


# ---------------------------------------------------------------------------
# Validation: empty string rejected
# ---------------------------------------------------------------------------


def test_predicate_form_empty_string_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn)

    with pytest.raises(HTTPException) as exc_info:
        _update_assertion_impl(aid, {"predicate_form": "   "})

    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Validation: >2000 chars rejected
# ---------------------------------------------------------------------------


def test_predicate_form_too_long_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn)

    with pytest.raises(HTTPException) as exc_info:
        _update_assertion_impl(aid, {"predicate_form": "x" * 2001})

    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Dispatch-op path coverage — exercises _op_assertion_update directly so the
# sentinel-default for predicate_form (and the resulting "absent vs explicit
# null" distinction at the dispatch boundary) is regression-tested.
# Without these the route-level clearing logic is structurally unreachable
# from the MCP surface, which is the only surface agents actually use.
# ---------------------------------------------------------------------------


def test_dispatch_op_clear_via_explicit_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops.ops_assertions import _op_assertion_update

    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="to-be-cleared")

    result = _op_assertion_update(assertion_id=aid, predicate_form=None)

    assert "error" not in result, result
    assert result["predicate_form"] is None
    assert _get_predicate_form(conn, aid) is None


def test_dispatch_op_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex_store.dispatch_ops.ops_assertions import _op_assertion_update

    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form=None)

    result = _op_assertion_update(assertion_id=aid, predicate_form="role(x, y, z)")

    assert "error" not in result, result
    assert result["predicate_form"] == "role(x, y, z)"
    assert _get_predicate_form(conn, aid) == "role(x, y, z)"


def test_dispatch_op_predicate_form_absent_preserves_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting predicate_form from kwargs (sentinel default) MUST NOT touch
    the column, even when other fields are being updated."""
    from cortex_store.dispatch_ops.ops_assertions import _op_assertion_update

    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="role(preserved, x, y)")

    result = _op_assertion_update(assertion_id=aid, review_status="staged")

    assert "error" not in result, result
    assert result["predicate_form"] == "role(preserved, x, y)"
    assert _get_predicate_form(conn, aid) == "role(preserved, x, y)"


def test_dispatch_op_only_predicate_form_clear_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch call with predicate_form=None as the sole field MUST NOT
    return 'No fields to update' — clearing is a real update."""
    from cortex_store.dispatch_ops.ops_assertions import _op_assertion_update

    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="role(value, x, y)")

    result = _op_assertion_update(assertion_id=aid, predicate_form=None)

    assert "error" not in result, result
    assert _get_predicate_form(conn, aid) is None
