"""Integration tests for the superseded_by overwrite idempotency guards
on both cortex.assertion_update and cortex.supersede call paths.

Acute trigger: C1 corruption in session claude-web-2026-05-16-0325 where
sonnet-4-6 bulk-apply silently clobbered assertion 3326.superseded_by from
9810 to 9815, orphaning 9810. Both call paths previously lacked any
idempotency guard. See todo:cortex-superseded-by-overwrite-guards and
friction 9824 (supersede) / 9825 (assertion_update).

Contract:
  - assertion_update(superseded_by=Y) on a row whose superseded_by IS NOT
    NULL → HTTP 409 (unless force=True).
  - supersede(old_assertion_id=X, ...) on an X whose superseded_by IS NOT
    NULL → HTTP 409 (unless force=True).
  - force=True bypasses both guards (escape hatch for known-intentional
    chain rewrites).
  - rejected supersede rolls back the just-INSERTed replacement row so no
    orphan rows accumulate from repeated rejections.

Concurrency: these tests sequence both writers via Python control flow on
a single in-memory connection. The cross-thread / cross-process atomicity
of the CAS (`WHERE id = ? AND superseded_by IS NULL` is a single-statement
SQLite operation, TOCTOU-free) is codified in
decision:cortex-api-write-serialization / assertion 9956 but not exercised
empirically here. The "another writer just won" shape is simulated by
pre-setting `superseded_by` before the call under test (which is what
`test_supersede_rejects_when_already_superseded` and
`test_update_rejects_overwrite_when_already_superseded` do); a true racing
test would require a file-backed DB and threading.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from cortex_store.routes.assertions import (
    _supersede_assertion_impl,
    _update_assertion_impl,
)

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
    chunk_id_schema TEXT,
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
    attributes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);
CREATE TABLE entities (id TEXT PRIMARY KEY, lifecycle TEXT, workflow_state TEXT);
CREATE TABLE session_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, agent TEXT, from_node TEXT, to_node TEXT,
    edge_type TEXT, strength REAL, edge_source TEXT, context TEXT
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_ASSERTIONS_DDL)
    conn.execute("INSERT INTO entities (id) VALUES (?)", ("test:entity",))
    conn.commit()
    return conn


def _insert(
    conn: sqlite3.Connection,
    *,
    superseded_by: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, superseded_by)"
        " VALUES (?, ?, ?, ?)",
        ("test:entity", "Test claim.", "believed", superseded_by),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _patch_update(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(
        "cortex_store.routes.assertions._update.cortex_conn", lambda: conn
    )


def _patch_supersede(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    # Route calls conn.close() in finally — wrap with a no-op close so the
    # in-memory DB survives for test assertions read-back.
    class _NoCloseConn:
        def __init__(self, c: sqlite3.Connection) -> None:
            self._c = c

        def __getattr__(self, name: str) -> object:
            return getattr(self._c, name)

        def close(self) -> None:
            return None

    wrapper = _NoCloseConn(conn)
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.cortex_conn", lambda: wrapper
    )
    # Side-effect modules touched by the route — neutralize to keep the
    # test focused on the guard semantics, not on enrichment plumbing.
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.enrich_background",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.enrich_old_assertion_events",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.reindex_assertion_fts",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede._embed_assertion_background",
        lambda *a, **kw: None,
    )

    class _FakeVS:
        @staticmethod
        def is_initialized() -> bool:
            return False

        @staticmethod
        def delete_assertion_embedding(_id: int) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.vector_store", _FakeVS
    )

    # belief_guard.analyze_assertion_impact returns a namespace whose
    # likely_supersedes / touched_assertions are read; stub minimally.
    class _FakeImpact:
        likely_supersedes: list[int] = []
        touched_assertions: list[object] = []

    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.analyze_assertion_impact",
        lambda *a, **kw: _FakeImpact(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.compute_entrenchment",
        lambda **kw: 0.5,
    )


# ---------------------------------------------------------------------------
# assertion_update — superseded_by overwrite guard
# ---------------------------------------------------------------------------


def test_update_rejects_overwrite_when_already_superseded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    _patch_update(monkeypatch, conn)
    successor1 = _insert(conn)
    successor2 = _insert(conn)
    target = _insert(conn, superseded_by=successor1)

    with pytest.raises(HTTPException) as exc:
        _update_assertion_impl(target, {"superseded_by": successor2})

    assert exc.value.status_code == 409
    row = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (target,)
    ).fetchone()
    assert row["superseded_by"] == successor1  # unchanged


def test_update_allows_overwrite_with_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    _patch_update(monkeypatch, conn)
    successor1 = _insert(conn)
    successor2 = _insert(conn)
    target = _insert(conn, superseded_by=successor1)

    result = _update_assertion_impl(
        target, {"superseded_by": successor2, "force": True}
    )

    assert result["superseded_by"] == successor2
    row = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (target,)
    ).fetchone()
    assert row["superseded_by"] == successor2


def test_update_first_set_succeeds_without_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    _patch_update(monkeypatch, conn)
    successor = _insert(conn)
    target = _insert(conn)  # superseded_by IS NULL

    result = _update_assertion_impl(target, {"superseded_by": successor})

    assert result["superseded_by"] == successor


# ---------------------------------------------------------------------------
# supersede — old_assertion_id overwrite guard
# ---------------------------------------------------------------------------


_BASE_SUPERSEDE_BODY: dict[str, object] = {
    "entity_id": "test:entity",
    "claim": "Replacement claim.",
    "confidence": "believed",
    "evidence": "test evidence",
    "session_id": "test-session",
    "agent": "test",
}


def test_supersede_rejects_when_already_superseded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    _patch_supersede(monkeypatch, conn)
    successor = _insert(conn)
    target = _insert(conn, superseded_by=successor)

    body = {**_BASE_SUPERSEDE_BODY, "old_assertion_id": target}
    pre_count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    with pytest.raises(HTTPException) as exc:
        _supersede_assertion_impl(body)

    assert exc.value.status_code == 409
    row = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (target,)
    ).fetchone()
    assert row["superseded_by"] == successor  # unchanged
    # Rejected supersede MUST roll back the new INSERT — otherwise repeated
    # rejections accumulate orphan replacement rows with no chain edge into
    # them. A regression that drops the rollback (e.g. an inadvertent
    # autocommit toggle) would leave post_count > pre_count.
    post_count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert post_count == pre_count


def test_supersede_allows_overwrite_with_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    _patch_supersede(monkeypatch, conn)
    successor = _insert(conn)
    target = _insert(conn, superseded_by=successor)

    body = {**_BASE_SUPERSEDE_BODY, "old_assertion_id": target, "force": True}
    result = _supersede_assertion_impl(body)

    new_id = result["new"]["id"]
    row = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (target,)
    ).fetchone()
    assert row["superseded_by"] == new_id


def test_supersede_first_chain_link_succeeds_without_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    _patch_supersede(monkeypatch, conn)
    target = _insert(conn)  # superseded_by IS NULL

    body = {**_BASE_SUPERSEDE_BODY, "old_assertion_id": target}
    result = _supersede_assertion_impl(body)

    new_id = result["new"]["id"]
    row = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (target,)
    ).fetchone()
    assert row["superseded_by"] == new_id


# ---------------------------------------------------------------------------
# predicate_form carryover — friction 9826 regression suite
# ---------------------------------------------------------------------------


def _insert_with_predicate_form(
    conn: sqlite3.Connection,
    *,
    predicate_form: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form)"
        " VALUES (?, ?, ?, ?)",
        ("test:entity", "Test claim.", "believed", predicate_form),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def test_supersede_carries_over_predicate_form_when_claim_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseding with the SAME claim and no explicit predicate_form preserves
    the predecessor's canonical form on the new row (friction 9826)."""
    conn = _make_conn()
    _patch_supersede(monkeypatch, conn)
    target = _insert_with_predicate_form(
        conn, predicate_form="status(test:entity, active)"
    )

    # claim matches the predecessor's ("Test claim.") → carryover branch
    body = {**_BASE_SUPERSEDE_BODY, "claim": "Test claim.", "old_assertion_id": target}
    result = _supersede_assertion_impl(body)

    new_id = result["new"]["id"]
    row = conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["predicate_form"] == "status(test:entity, active)"


def test_supersede_drops_predicate_form_when_claim_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseding with a CHANGED claim and no explicit predicate_form must NOT
    clone the predecessor's form (which encodes the old claim). It is dropped to
    NULL; the async predicate-extract re-derives from the new claim (thread 1227).
    """
    conn = _make_conn()
    _patch_supersede(monkeypatch, conn)
    target = _insert_with_predicate_form(
        conn,
        predicate_form="has_attribute(test:entity, recovery_envelope_high, 27227.4)",
    )

    # _BASE_SUPERSEDE_BODY.claim = "Replacement claim." ≠ "Test claim." → drop branch
    body = {**_BASE_SUPERSEDE_BODY, "old_assertion_id": target}
    result = _supersede_assertion_impl(body)

    new_id = result["new"]["id"]
    row = conn.execute(
        "SELECT predicate_form, raw_predicate_form, normalizer_version "
        "FROM assertions WHERE id = ?",
        (new_id,),
    ).fetchone()
    assert row["predicate_form"] is None
    assert row["raw_predicate_form"] is None
    assert row["normalizer_version"] is None


def test_supersede_explicit_predicate_form_overrides_on_claim_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit predicate_form in the supersede call is honoured (normalised)
    even when the claim changed — the drop branch only applies to inherited forms.
    """
    conn = _make_conn()
    _patch_supersede(monkeypatch, conn)
    target = _insert_with_predicate_form(
        conn, predicate_form="status(test:entity, active)"
    )

    body = {
        **_BASE_SUPERSEDE_BODY,
        "old_assertion_id": target,
        "predicate_form": "status(test:entity, archived)",
    }
    result = _supersede_assertion_impl(body)

    new_id = result["new"]["id"]
    row = conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["predicate_form"] == "status(test:entity, archived)"


def test_supersede_drops_predicate_form_on_explicit_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing predicate_form=null explicitly must clear it on the new row
    (intentional drop semantics — spec §7.3 clone-then-override)."""
    conn = _make_conn()
    _patch_supersede(monkeypatch, conn)
    target = _insert_with_predicate_form(
        conn, predicate_form="status(test:entity, active)"
    )

    body = {**_BASE_SUPERSEDE_BODY, "old_assertion_id": target, "predicate_form": None}
    result = _supersede_assertion_impl(body)

    new_id = result["new"]["id"]
    row = conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["predicate_form"] is None


def test_supersede_null_predicate_form_stays_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseding an assertion with predicate_form=NULL produces a new row
    with predicate_form=NULL (carryover of null is null)."""
    conn = _make_conn()
    _patch_supersede(monkeypatch, conn)
    target = _insert_with_predicate_form(conn, predicate_form=None)

    body = {**_BASE_SUPERSEDE_BODY, "old_assertion_id": target}
    result = _supersede_assertion_impl(body)

    new_id = result["new"]["id"]
    row = conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["predicate_form"] is None
