"""Tests for the force=True + supersedes_id CAS path on POST /assertions.

Closes W-new-1 from agent-bus threads 1064 / 1065. The Pass 1 W8 fix to
``libs/cortex_store/routes/assertions/_create.py`` (~lines 242-281) added a
CAS-on-UPDATE guard for the lineage-pointer write that the
``force=True + supersedes_id`` codepath performs after the INSERT::

    UPDATE assertions SET superseded_by = ?, valid_until = ?, updated_at = ?
    WHERE id = ? AND superseded_by IS NULL

When the predicate misses (target row already part of a supersession chain,
or deleted concurrently), the route MUST roll back the just-INSERTed
replacement row and raise 404/409 — otherwise the chain pointer either
silently no-ops (pre-fix behaviour) or leaves a dangling lineage edge.

``test_superseded_by_overwrite_guards.py`` exercises the analogous paths in
``_supersede.py`` and ``_update.py`` but not the ``_create.py`` codepath;
this module closes that gap. Filed as
``todo:test-create-force-supersedes-cas-coverage``.

Concurrency note: the CAS UPDATE is a single-statement SQLite op
(``WHERE id = ? AND superseded_by IS NULL``), TOCTOU-free; the
cross-thread / cross-process race shape is codified in
``decision:cortex-api-write-serialization`` / assertion 9956 but not
exercised empirically here. The "another writer just won" shape is
simulated by pre-setting ``superseded_by`` before the call under test, same
pattern as the supersede tests.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import HTTPException

from cortex_store.routes.assertions import _create_assertion_impl

# Schema MUST include ``claim_hash`` — the _create route writes it on every
# INSERT (used by the dedup-fallback SELECT when ``INSERT OR IGNORE`` no-ops).
# test_superseded_by_overwrite_guards.py's DDL omits it because supersede
# doesn't write through this column.
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
    chunk_id TEXT,
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
    claim_hash TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);
CREATE TABLE entities (id TEXT PRIMARY KEY);
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
        "INSERT INTO assertions (entity_id, claim, confidence, superseded_by, claim_hash)"
        " VALUES (?, ?, ?, ?, ?)",
        ("test:entity", "Seed claim.", "believed", superseded_by, "stub-hash-seed"),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Side-effect stubs — the test focuses on the CAS guard, not on
# validation / quality / entrenchment / contradiction-check plumbing.
# ---------------------------------------------------------------------------


@dataclass
class _StubValidation:
    rejected: bool = False
    route_to_staging: bool = False
    quality_score: float = 0.9
    hard_reject: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class _StubGuard:
    allowed: bool = True
    block_detail: str = ""
    review_status: str | None = None
    contradiction_warnings: list = field(default_factory=list)


@dataclass
class _StubContradiction:
    flagged: bool = False
    review_notes: str = ""
    contradicting_entity: str = ""
    edge_id: int = 0


class _NoCloseConn:
    """Wrapper that no-ops .close() so the in-memory DB outlives the route call."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def close(self) -> None:
        return None


class _NoOpThread:
    """Replaces threading.Thread inside _create so background reindex never spawns."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def start(self) -> None:
        return None


@pytest.fixture
def conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """In-memory sqlite + stubbed side-effects in cortex_store.routes.assertions._create."""
    c = _make_conn()
    wrapper = _NoCloseConn(c)

    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.cortex_conn",
        lambda: wrapper,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.validate_assertion",
        lambda body: _StubValidation(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.check_confirmed_validatability",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.guard_assertion_write",
        lambda *a, **kw: _StubGuard(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.compute_entrenchment",
        lambda **kw: 0.5,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.check_contradictions",
        lambda *a, **kw: _StubContradiction(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.check_near_duplicate",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.record_near_duplicate",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.enrich_background",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.reindex_assertion_fts",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create._embed_assertion_background",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.dispatch_predicate_extract_background",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.threading.Thread",
        _NoOpThread,
    )

    yield c


_BASE_CREATE_BODY: dict[str, object] = {
    "entity_id": "test:entity",
    "claim": "Replacement claim via create+force.",
    "confidence": "believed",
    "evidence": "test evidence",
    "derivation_type": "inference",
    "force": True,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_force_supersedes_id_already_superseded_returns_409(
    conn: sqlite3.Connection,
) -> None:
    """force=True + supersedes_id pointing at an already-superseded target
    raises 409 and rolls back the just-INSERTed replacement row.

    Regression: pre-W8-fix, the lineage UPDATE silently no-op'd (no CAS),
    leaving the new INSERT in place with no lineage edge into it — a
    dangling replacement row. Rollback invariant: post_count == pre_count.
    """
    successor = _insert(conn)
    target = _insert(conn, superseded_by=successor)

    pre_count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    body = {**_BASE_CREATE_BODY, "supersedes_id": target}
    with pytest.raises(HTTPException) as exc:
        _create_assertion_impl(body)

    assert exc.value.status_code == 409
    # Target's lineage pointer was not overwritten.
    row = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (target,)
    ).fetchone()
    assert row["superseded_by"] == successor
    # The rejected create MUST roll back the new INSERT — mirrors the
    # supersede rollback invariant in test_supersede_rejects_when_already_superseded.
    post_count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert post_count == pre_count


def test_create_force_supersedes_id_missing_returns_404(
    conn: sqlite3.Connection,
) -> None:
    """force=True + supersedes_id pointing at a non-existent assertion
    raises 404 (early-existence-check path).

    The CAS-404 race path (target deleted between pre-check and CAS) is
    structurally analogous to _supersede.py's tested 409 path and is
    covered there indirectly via the same `WHERE id = ? AND
    superseded_by IS NULL` predicate; a deterministic in-memory test for
    the race would require file-backed sqlite + threading, out of scope.
    """
    body = {**_BASE_CREATE_BODY, "supersedes_id": 99999}
    with pytest.raises(HTTPException) as exc:
        _create_assertion_impl(body)

    assert exc.value.status_code == 404
    # No row was created for the failed attempt.
    count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert count == 0


def test_create_force_supersedes_id_first_chain_link_succeeds(
    conn: sqlite3.Connection,
) -> None:
    """force=True + supersedes_id pointing at an active (superseded_by IS NULL)
    target succeeds and writes the lineage pointer (positive path).

    Confirms the CAS guard does NOT spuriously reject valid first-chain
    supersedes via the create+force codepath.
    """
    target = _insert(conn)  # superseded_by IS NULL

    body = {**_BASE_CREATE_BODY, "supersedes_id": target}
    result = _create_assertion_impl(body)

    new_id = result["item"]["id"]
    row = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (target,)
    ).fetchone()
    assert row["superseded_by"] == new_id
