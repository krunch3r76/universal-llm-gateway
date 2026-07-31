"""Unit tests for the landed_claim_not_on_master audit detector (thread 1153).

Verifies the §5 algorithm (phantom / not_reachable classification) and the §6
false-positive guards: superseded → skip; non-committed review_status → skip;
land-asserting context token required (bare SHA mention does not fire); local
master only (origin-lag is silent); worker-unreachable degrades to silent;
structured-provenance attribute preferred over claim regex.

Grounded in: case:telemetry-vs-git-ground-truth-divergence (assertion 11575).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops._detectors import git_reconcile
from cortex_store.dispatch_ops._detectors.git_reconcile import (
    detect_landed_claim_not_on_master,
)

_KIND = "landed_claim_not_on_master"
_SHA_A = "a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0"
_SHA_B = "0f1e2d3c4b5a60718293a4b5c6d7e8f900112233"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT,
            attributes TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            superseded_by INTEGER,
            review_status TEXT DEFAULT 'committed'
        );
        """
    )
    return c


def _add_entity(conn, eid: str, attributes: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) VALUES (?, 'decision', ?, ?)",
        (eid, eid, json.dumps(attributes) if attributes else None),
    )


def _add_assertion(
    conn,
    entity_id: str,
    claim: str,
    *,
    superseded_by: int | None = None,
    review_status: str = "committed",
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, superseded_by, review_status) "
        "VALUES (?, ?, ?, ?)",
        (entity_id, claim, superseded_by, review_status),
    )
    return int(cur.lastrowid)


@pytest.fixture()
def reach(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    """Stub the worker reachability probe with a per-SHA lookup table."""
    table: dict[str, dict] = {}

    def _fake(sha: str) -> dict | None:
        return table.get(sha)

    monkeypatch.setattr(git_reconcile, "_reachable_via_worker", _fake)
    return table


# --- fires (one finding) -------------------------------------------------


def test_not_reachable_fires(conn, reach) -> None:
    _add_entity(conn, "decision:d1")
    aid = _add_assertion(conn, "decision:d1", f"Phase landed as merge_commit {_SHA_A}")
    reach[_SHA_A] = {"sha": _SHA_A, "exists": True, "reachable": False}
    findings = detect_landed_claim_not_on_master(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == _KIND
    assert f["subject"] == "decision:d1"
    assert f["severity"] == "critical"
    assert "not_reachable" in f["detail"]
    assert str(aid) in f["detail"]


def test_phantom_sha_fires(conn, reach) -> None:
    _add_entity(conn, "decision:d1")
    _add_assertion(conn, "decision:d1", f"landed; master_sha {_SHA_A}")
    reach[_SHA_A] = {"sha": _SHA_A, "exists": False, "reachable": False}
    findings = detect_landed_claim_not_on_master(conn)
    assert len(findings) == 1
    assert "phantom" in findings[0]["detail"]


# --- silent (no finding) -------------------------------------------------


def test_reachable_sha_no_finding(conn, reach) -> None:
    _add_entity(conn, "decision:d1")
    _add_assertion(conn, "decision:d1", f"landed; master_sha {_SHA_A}")
    reach[_SHA_A] = {"sha": _SHA_A, "exists": True, "reachable": True}
    assert detect_landed_claim_not_on_master(conn) == []


def test_superseded_assertion_skipped(conn, reach) -> None:
    _add_entity(conn, "decision:d1")
    _add_assertion(
        conn, "decision:d1", f"landed; master_sha {_SHA_A}", superseded_by=999
    )
    reach[_SHA_A] = {"sha": _SHA_A, "exists": True, "reachable": False}
    assert detect_landed_claim_not_on_master(conn) == []


def test_non_committed_review_status_skipped(conn, reach) -> None:
    _add_entity(conn, "decision:d1")
    _add_assertion(
        conn, "decision:d1", f"landed; master_sha {_SHA_A}", review_status="rejected"
    )
    reach[_SHA_A] = {"sha": _SHA_A, "exists": True, "reachable": False}
    assert detect_landed_claim_not_on_master(conn) == []


def test_bare_sha_mention_no_context_token_skipped(conn, reach) -> None:
    """A SHA with no land-asserting context token must NOT fire (§6 guard 2)."""
    _add_entity(conn, "decision:d1")
    _add_assertion(conn, "decision:d1", f"bug was introduced in {_SHA_A}, then fixed")
    reach[_SHA_A] = {"sha": _SHA_A, "exists": True, "reachable": False}
    assert detect_landed_claim_not_on_master(conn) == []


def test_local_master_yes_origin_no_no_finding(conn, reach) -> None:
    """Local-master reachable even though claim notes origin lag (§6 guard 3)."""
    _add_entity(conn, "decision:d1")
    _add_assertion(
        conn,
        "decision:d1",
        f"landed: master CAS-advanced locally to {_SHA_A}, NOT yet pushed to origin",
    )
    reach[_SHA_A] = {"sha": _SHA_A, "exists": True, "reachable": True}
    assert detect_landed_claim_not_on_master(conn) == []


def test_worker_unreachable_degrades_silent(conn, reach) -> None:
    """Probe returning None (infra down) yields no finding — advisory only."""
    _add_entity(conn, "decision:d1")
    _add_assertion(conn, "decision:d1", f"landed; master_sha {_SHA_A}")
    # reach table empty → _fake returns None for _SHA_A
    assert detect_landed_claim_not_on_master(conn) == []


# --- structured-provenance preference (§7) -------------------------------


def test_typed_attribute_preferred_over_claim_regex(conn, reach) -> None:
    """When the entity carries a typed master_sha attribute, it is reconciled —
    not whatever SHA happens to appear in the prose."""
    _add_entity(conn, "decision:d1", attributes={"master_sha": _SHA_B})
    # Claim prose mentions a *different* (reachable) SHA; the typed attr wins.
    _add_assertion(conn, "decision:d1", f"landed (see earlier merge_commit {_SHA_A})")
    reach[_SHA_A] = {"sha": _SHA_A, "exists": True, "reachable": True}
    reach[_SHA_B] = {"sha": _SHA_B, "exists": True, "reachable": False}
    findings = detect_landed_claim_not_on_master(conn)
    assert len(findings) == 1
    assert _SHA_B in findings[0]["detail"]
    assert _SHA_A not in findings[0]["detail"]


# --- subject scoping -----------------------------------------------------


def test_subject_filter_scopes_to_one_entity(conn, reach) -> None:
    _add_entity(conn, "decision:d1")
    _add_entity(conn, "decision:d2")
    _add_assertion(conn, "decision:d1", f"landed; master_sha {_SHA_A}")
    _add_assertion(conn, "decision:d2", f"landed; master_sha {_SHA_B}")
    reach[_SHA_A] = {"sha": _SHA_A, "exists": True, "reachable": False}
    reach[_SHA_B] = {"sha": _SHA_B, "exists": True, "reachable": False}
    findings = detect_landed_claim_not_on_master(conn, subject="decision:d1")
    assert len(findings) == 1
    assert findings[0]["subject"] == "decision:d1"
