"""Hermetic tests for prod FK orphan repair SQL and salience stale-cache purge."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.entity_crud import create_entity_impl
from cortex_store.salience import compute_all_salience

_MIG_PATH = (
    Path(__file__).parent / "migrations" / "058_relationship_types_inverse_backfill.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_058_relationship_types_inverse_backfill", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_058 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_058)

_F5_BACKFILL_SQL = """
INSERT OR IGNORE INTO relationship_types (type, description, inverse) VALUES
  ('represented_by', 'Inverse of represents', 'represents'),
  ('employs', 'Inverse of employed_by', 'employed_by'),
  ('owned_by', 'Inverse of owns', 'owns'),
  ('followed_by', 'Inverse of preceded_by', 'preceded_by'),
  ('received_payment', 'Inverse of payment_on', 'payment_on'),
  ('amended_by', 'Inverse of amends', 'amends')
"""


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    return migrated_conn


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
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, 0.1)",
        (entity_id, claim, claim_hash),
    )
    return int(cur.lastrowid)


def _global_fk_violations(conn: sqlite3.Connection) -> int:
    return len(conn.execute("PRAGMA foreign_key_check").fetchall())


def _run_repair_txn(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA defer_foreign_keys=ON")
    migration_058.migrate(conn)
    conn.execute(
        "UPDATE relationships SET type = 'references', "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE type = 'about'"
    )
    conn.execute(
        "DELETE FROM relationships WHERE from_entity NOT IN (SELECT id FROM entities) "
        "OR to_entity NOT IN (SELECT id FROM entities)"
    )
    conn.execute(
        "DELETE FROM near_duplicate_flags "
        "WHERE assertion_id NOT IN (SELECT id FROM assertions) "
        "OR duplicate_of NOT IN (SELECT id FROM assertions)"
    )
    conn.execute(
        "DELETE FROM entity_salience_cache "
        "WHERE entity_id NOT IN (SELECT id FROM entities)"
    )
    assert _global_fk_violations(conn) == 0
    conn.commit()


def _seed_bad_row(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(sql, params)
    conn.execute("PRAGMA foreign_keys=ON")


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
def test_f5_inverse_backfill_clears_self_fk(conn: sqlite3.Connection) -> None:
    _seed_bad_row(
        conn,
        "INSERT INTO relationship_types (type, description, inverse) "
        "VALUES ('represents', 'Represents', 'represented_by')",
    )
    conn.commit()
    assert _global_fk_violations(conn) > 0
    migration_058.migrate(conn)
    conn.commit()
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_f4_about_repoint_clears_type_fk(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "document:about-src")
    _seed_entity(conn, "document:about-dst")
    _seed_bad_row(
        conn,
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('document:about-src', 'document:about-dst', 'about', 1)",
    )
    conn.commit()
    assert _global_fk_violations(conn) > 0
    conn.execute("UPDATE relationships SET type = 'references' WHERE type = 'about'")
    conn.commit()
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_f3_entity_orphan_relationship_delete(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "document:orphan-src")
    _seed_entity(conn, "property:orphan-target")
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('document:orphan-src', 'property:orphan-target', 'references', 1)"
    )
    _hard_delete_entity(conn, "document:orphan-src")
    conn.commit()
    assert _global_fk_violations(conn) > 0
    conn.execute(
        "DELETE FROM relationships WHERE from_entity NOT IN (SELECT id FROM entities) "
        "OR to_entity NOT IN (SELECT id FROM entities)"
    )
    conn.commit()
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_f2_near_duplicate_flags_orphan_delete(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "project:dup-a")
    _seed_entity(conn, "project:dup-b")
    a_id = _seed_assertion(conn, "project:dup-a")
    b_id = _seed_assertion(conn, "project:dup-b")
    conn.execute(
        "INSERT INTO near_duplicate_flags (assertion_id, duplicate_of, score) "
        "VALUES (?, ?, 0.9)",
        (a_id, b_id),
    )
    _hard_delete_assertions(conn, (a_id, b_id))
    conn.commit()
    assert _global_fk_violations(conn) > 0
    conn.execute(
        "DELETE FROM near_duplicate_flags "
        "WHERE assertion_id NOT IN (SELECT id FROM assertions) "
        "OR duplicate_of NOT IN (SELECT id FROM assertions)"
    )
    conn.commit()
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_f1_salience_cache_orphan_delete(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "artifact:gone")
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score, fingerprint) "
        "VALUES ('artifact:gone', 0.1, 'fp-gone')"
    )
    _hard_delete_entity(conn, "artifact:gone")
    conn.commit()
    assert _global_fk_violations(conn) > 0
    conn.execute(
        "DELETE FROM entity_salience_cache "
        "WHERE entity_id NOT IN (SELECT id FROM entities)"
    )
    conn.commit()
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_combined_repair_txn_clears_all_orphan_classes(
    conn: sqlite3.Connection,
) -> None:
    _seed_entity(conn, "property:combo-target")
    _seed_entity(conn, "document:combo-live")
    _seed_entity(conn, "document:combo-missing")
    _seed_entity(conn, "artifact:combo-gone")
    _seed_bad_row(
        conn,
        "INSERT INTO relationship_types (type, description, inverse) "
        "VALUES ('represents', 'Represents', 'represented_by')",
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES ('document:combo-live', 'property:combo-target', 'references', 1), "
        "('document:combo-missing', 'property:combo-target', 'references', 1)"
    )
    _seed_bad_row(
        conn,
        "UPDATE relationships SET type = 'about' "
        "WHERE from_entity = 'document:combo-live'",
    )
    a_id = _seed_assertion(conn, "project:combo-a")
    b_id = _seed_assertion(conn, "project:combo-b")
    conn.execute(
        "INSERT INTO near_duplicate_flags (assertion_id, duplicate_of, score) "
        "VALUES (?, ?, 0.8)",
        (a_id, b_id),
    )
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score, fingerprint) "
        "VALUES ('artifact:combo-gone', 0.2, 'fp-combo')"
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "DELETE FROM entities WHERE id IN (?, ?)",
        ("document:combo-missing", "artifact:combo-gone"),
    )
    conn.execute("PRAGMA foreign_keys=ON")
    _hard_delete_assertions(conn, (a_id, b_id))
    conn.commit()
    assert _global_fk_violations(conn) > 0
    _run_repair_txn(conn)
    assert _global_fk_violations(conn) == 0


@pytest.mark.offline
def test_compute_all_salience_force_purges_stale_cache(
    conn: sqlite3.Connection,
) -> None:
    _seed_entity(conn, "project:salience-live")
    _seed_entity(conn, "artifact:salience-stale")
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score, fingerprint) "
        "VALUES ('artifact:salience-stale', 0.3, 'fp-stale')"
    )
    _hard_delete_entity(conn, "artifact:salience-stale")
    conn.commit()
    assert _global_fk_violations(conn) > 0
    compute_all_salience(conn, force=True)
    conn.commit()
    assert _global_fk_violations(conn) == 0
    stale = conn.execute(
        "SELECT 1 FROM entity_salience_cache WHERE entity_id = 'artifact:salience-stale'"
    ).fetchone()
    assert stale is None
