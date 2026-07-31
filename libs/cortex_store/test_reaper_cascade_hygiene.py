"""Hermetic tests for reaper cascade FK hygiene helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from cortex_store.cascade_hygiene import (
    apply_reap_consistency_hygiene,
    purge_fk_orphans,
)
from cortex_store.claim_hash import compute_claim_hash
from cortex_store.entity_crud import create_entity_impl
from cortex_store.entity_id_registry import audit_entity_id_registry_coverage
from cortex_store.routes.reaper import (
    _DEFAULT_ENTRENCHMENT_THRESHOLD,
    _DEFAULT_TTL_DAYS,
    _find_candidates,
    _reap_entity,
)


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    return migrated_conn


def _global_fk_violations(conn: sqlite3.Connection) -> int:
    return len(conn.execute("PRAGMA foreign_key_check").fetchall())


def _seed_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    create_entity_impl(
        conn,
        {
            "id": entity_id,
            "type": entity_id.split(":", 1)[0],
            "name": entity_id.split(":", 1)[-1],
        },
    )


def _seed_assertion(conn: sqlite3.Connection, entity_id: str) -> int:
    claim = f"claim for {entity_id}"
    claim_hash = compute_claim_hash(entity_id, claim)
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, entrenchment_score) "
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, 0.01)",
        (entity_id, claim, claim_hash),
    )
    return int(cur.lastrowid)


def _seed_ephemeral(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    ttl_days: int = 30,
    days_stale: int = 60,
) -> None:
    _seed_entity(conn, entity_id)
    stale_at = (datetime.now(UTC) - timedelta(days=days_stale)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute(
        "UPDATE entities SET retention_policy = 'ephemeral', "
        "retention_ttl_days = ?, last_accessed_at = ?, lifecycle = 'active' "
        "WHERE id = ?",
        (ttl_days, stale_at, entity_id),
    )


def _hard_delete_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
    conn.execute("PRAGMA foreign_keys=ON")


def _hard_delete_assertions(
    conn: sqlite3.Connection, assertion_ids: tuple[int, ...]
) -> None:
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executemany(
        "DELETE FROM assertions WHERE id = ?", ((i,) for i in assertion_ids)
    )
    conn.execute("PRAGMA foreign_keys=ON")


@pytest.mark.offline
def test_reap_consistency_deactivates_relationships_and_drops_salience(
    conn: sqlite3.Connection,
) -> None:
    _seed_ephemeral(conn, "note:ephemeral-target")
    _seed_entity(conn, "project:permanent-partner")
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('note:ephemeral-target', 'project:permanent-partner', 'references', 1), "
        "('project:permanent-partner', 'note:ephemeral-target', 'references', 1)"
    )
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score, fingerprint) "
        "VALUES ('note:ephemeral-target', 0.2, 'fp-ephemeral')"
    )
    a_id = _seed_assertion(conn, "note:ephemeral-target")
    b_id = _seed_assertion(conn, "project:permanent-partner")
    conn.execute(
        "INSERT INTO near_duplicate_flags (assertion_id, duplicate_of, score) "
        "VALUES (?, ?, 0.85)",
        (a_id, b_id),
    )
    conn.commit()

    now_iso = "2026-06-10T12:00:00Z"
    counts = _reap_entity(conn, "note:ephemeral-target", now_iso)
    conn.commit()

    assert counts["relationships_deactivated"] == 2
    assert counts["salience_rows_dropped"] == 1
    rel_rows = conn.execute(
        "SELECT active FROM relationships "
        "WHERE from_entity = 'note:ephemeral-target' OR to_entity = 'note:ephemeral-target'"
    ).fetchall()
    assert len(rel_rows) == 2
    assert all(r[0] == 0 for r in rel_rows)
    assert (
        conn.execute(
            "SELECT 1 FROM entity_salience_cache WHERE entity_id = 'note:ephemeral-target'"
        ).fetchone()
        is None
    )
    closed = conn.execute(
        "SELECT valid_until FROM assertions WHERE entity_id = 'note:ephemeral-target'"
    ).fetchone()
    assert closed is not None and closed[0] == now_iso
    assert (
        conn.execute(
            "SELECT 1 FROM near_duplicate_flags WHERE assertion_id = ?", (a_id,)
        ).fetchone()
        is not None
    )
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_purge_fk_orphans_clears_each_class(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "property:orphan-target")
    _seed_entity(conn, "document:orphan-src")
    _seed_entity(conn, "project:tag-live")
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('document:orphan-src', 'property:orphan-target', 'references', 1)"
    )
    _seed_entity(conn, "project:dup-a")
    _seed_entity(conn, "project:dup-b")
    a_id = _seed_assertion(conn, "project:dup-a")
    b_id = _seed_assertion(conn, "project:dup-b")
    conn.execute(
        "INSERT INTO near_duplicate_flags (assertion_id, duplicate_of, score) "
        "VALUES (?, ?, 0.9)",
        (a_id, b_id),
    )
    _seed_entity(conn, "artifact:gone")
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score, fingerprint) "
        "VALUES ('artifact:gone', 0.1, 'fp-gone')"
    )
    live_assertion = _seed_assertion(conn, "project:tag-live")
    conn.execute(
        "INSERT INTO tag_assignments (tag_name, entity_id, assertion_id, assigned_by) "
        "VALUES ('orphan-tag', 'artifact:gone', ?, 'test')",
        (live_assertion,),
    )
    _hard_delete_entity(conn, "document:orphan-src")
    _hard_delete_entity(conn, "artifact:gone")
    _hard_delete_assertions(conn, (a_id, b_id))
    conn.commit()
    assert _global_fk_violations(conn) > 0

    counts = purge_fk_orphans(conn)
    conn.commit()

    assert counts["relationships"] >= 1
    assert counts["near_duplicate_flags"] >= 1
    assert counts["entity_salience_cache"] >= 1
    assert counts["tag_assignments"] >= 1
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_purge_fk_orphans_idempotent(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "artifact:gone")
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score, fingerprint) "
        "VALUES ('artifact:gone', 0.1, 'fp-gone')"
    )
    _hard_delete_entity(conn, "artifact:gone")
    conn.commit()
    assert _global_fk_violations(conn) > 0

    first = purge_fk_orphans(conn)
    conn.commit()
    assert first["entity_salience_cache"] == 1
    assert _global_fk_violations(conn) == 0

    second = purge_fk_orphans(conn)
    assert all(v == 0 for v in second.values())
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_cascade_helper_preserves_relationship_provenance(
    conn: sqlite3.Connection,
) -> None:
    _seed_entity(conn, "note:ephemeral")
    _seed_entity(conn, "project:partner")
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('note:ephemeral', 'project:partner', 'references', 1)"
    )
    conn.commit()

    counts = apply_reap_consistency_hygiene(
        conn, "note:ephemeral", "2026-06-10T00:00:00Z"
    )
    assert counts["relationships_deactivated"] == 1
    row = conn.execute(
        "SELECT active, from_entity, to_entity FROM relationships "
        "WHERE from_entity = 'note:ephemeral'"
    ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert row[1] == "note:ephemeral"
    assert row[2] == "project:partner"


@pytest.mark.offline
def test_apply_reap_consistency_hygiene_empty(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "note:empty")
    conn.commit()
    counts = apply_reap_consistency_hygiene(conn, "note:empty", "2026-06-10T00:00:00Z")
    assert counts == {"relationships_deactivated": 0, "salience_rows_dropped": 0}


@pytest.mark.offline
def test_entity_id_registry_coverage(conn: sqlite3.Connection) -> None:
    assert audit_entity_id_registry_coverage(conn) == []


@pytest.mark.offline
def test_reap_protected_by_active_relationship_from_permanent_inbound(
    conn: sqlite3.Connection,
) -> None:
    _seed_ephemeral(conn, "note:protected-in")
    _seed_entity(conn, "project:protector-in")
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('project:protector-in', 'note:protected-in', 'references', 1)"
    )
    conn.commit()

    candidates = _find_candidates(
        conn, _DEFAULT_TTL_DAYS, _DEFAULT_ENTRENCHMENT_THRESHOLD
    )
    match = next(c for c in candidates if c.entity_id == "note:protected-in")
    assert match.protected_by == "project:protector-in"

    conn.execute(
        "UPDATE relationships SET active = 0 WHERE from_entity = 'project:protector-in'"
    )
    conn.commit()
    candidates = _find_candidates(
        conn, _DEFAULT_TTL_DAYS, _DEFAULT_ENTRENCHMENT_THRESHOLD
    )
    match = next(c for c in candidates if c.entity_id == "note:protected-in")
    assert match.protected_by is None


@pytest.mark.offline
def test_reap_protected_by_active_relationship_from_permanent_outbound(
    conn: sqlite3.Connection,
) -> None:
    _seed_ephemeral(conn, "note:protected-out")
    _seed_entity(conn, "project:protector-out")
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('note:protected-out', 'project:protector-out', 'references', 1)"
    )
    conn.commit()

    candidates = _find_candidates(
        conn, _DEFAULT_TTL_DAYS, _DEFAULT_ENTRENCHMENT_THRESHOLD
    )
    match = next(c for c in candidates if c.entity_id == "note:protected-out")
    assert match.protected_by == "project:protector-out"


@pytest.mark.offline
def test_reap_skipped_while_protected_then_runs_after_deactivation(
    conn: sqlite3.Connection,
) -> None:
    _seed_ephemeral(conn, "note:run-protected")
    _seed_entity(conn, "project:run-protector")
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('project:run-protector', 'note:run-protected', 'references', 1)"
    )
    conn.commit()

    candidates = _find_candidates(
        conn, _DEFAULT_TTL_DAYS, _DEFAULT_ENTRENCHMENT_THRESHOLD
    )
    protected = next(c for c in candidates if c.entity_id == "note:run-protected")
    assert protected.protected_by == "project:run-protector"
    reapable = [c for c in candidates if c.protected_by is None]
    assert "note:run-protected" not in {c.entity_id for c in reapable}

    conn.execute(
        "UPDATE relationships SET active = 0 "
        "WHERE from_entity = 'project:run-protector'"
    )
    conn.commit()

    candidates = _find_candidates(
        conn, _DEFAULT_TTL_DAYS, _DEFAULT_ENTRENCHMENT_THRESHOLD
    )
    match = next(c for c in candidates if c.entity_id == "note:run-protected")
    assert match.protected_by is None

    now_iso = "2026-06-10T12:00:00Z"
    counts = _reap_entity(conn, "note:run-protected", now_iso)
    conn.commit()
    assert counts["assertions_closed"] >= 0
    row = conn.execute(
        "SELECT lifecycle FROM entities WHERE id = 'note:run-protected'"
    ).fetchone()
    assert row is not None and row[0] == "reaped"
