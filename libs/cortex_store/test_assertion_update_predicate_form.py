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
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_ASSERTIONS_DDL)
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

    result = _update_assertion_impl(aid, {"predicate_form": "X is-a Y"})

    assert result["predicate_form"] == "X is-a Y"
    assert _get_predicate_form(conn, aid) == "X is-a Y"


# ---------------------------------------------------------------------------
# value → value (overwrite existing)
# ---------------------------------------------------------------------------


def test_predicate_form_value_to_value(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="old_predicate")

    result = _update_assertion_impl(aid, {"predicate_form": "new_predicate"})

    assert result["predicate_form"] == "new_predicate"
    assert _get_predicate_form(conn, aid) == "new_predicate"


# ---------------------------------------------------------------------------
# value → NULL (clear via explicit null in dict body)
# ---------------------------------------------------------------------------


def test_predicate_form_clear_via_explicit_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="to-be-cleared")

    # Explicit null in dict → clearing intent
    result = _update_assertion_impl(aid, {"predicate_form": None})

    assert result["predicate_form"] is None
    assert _get_predicate_form(conn, aid) is None


# ---------------------------------------------------------------------------
# omit predicate_form → no change (other field updated, predicate_form untouched)
# ---------------------------------------------------------------------------


def test_predicate_form_untouched_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _make_conn()
    _patch(monkeypatch, conn)
    aid = _insert_assertion(conn, predicate_form="preserved")

    result = _update_assertion_impl(aid, {"review_status": "staged"})

    assert result["predicate_form"] == "preserved"
    assert _get_predicate_form(conn, aid) == "preserved"


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
