"""Acceptance tests for entity_rekey / entity_merge primitives."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.dispatch_ops.ops_assertions import _op_assertions
from cortex_store.dispatch_ops.ops_entities import _op_entity_get, _op_entity_update
from cortex_store.dispatch_ops.ops_misc import _op_tag_list
from cortex_store.dispatch_ops.ops_relationships import _op_relationships
from cortex_store.entity_aliases import resolve_entity_reference
from cortex_store.entity_crud import create_entity_impl
from cortex_store.entity_id_registry import audit_entity_id_registry_coverage
from cortex_store.entity_merge import entity_merge_impl
from cortex_store.entity_rekey_core import check_foreign_keys, entity_rekey_impl


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    return migrated_conn


def _seed_entity(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    entity_type: str = "project",
    name: str | None = None,
) -> None:
    create_entity_impl(
        conn,
        {
            "id": entity_id,
            "type": entity_type,
            "name": name or entity_id.split(":", 1)[-1],
        },
    )


def _seed_full_surface(
    conn: sqlite3.Connection, entity_id: str, *, peer_id: str
) -> int:
    _seed_entity(conn, entity_id)
    claim = f"claim for {entity_id}"
    claim_hash = compute_claim_hash(entity_id, claim)
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, entrenchment_score) "
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, 0.42)",
        (entity_id, claim, claim_hash),
    )
    assertion_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES (?, ?, 'evidence_for', 1)",
        (entity_id, peer_id),
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES (?, ?, 'evidence_for', 1)",
        (peer_id, entity_id),
    )
    conn.execute(
        "UPDATE entities SET aliases = ? WHERE id = ?",
        (json.dumps([f"alias-{entity_id}"]), entity_id),
    )
    conn.execute(
        "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, 'project', ?)",
        (entity_id, f"alias-{entity_id}"),
    )
    conn.execute(
        "INSERT INTO surface_forms (mention, entity_id, context_hash) VALUES (?, ?, ?)",
        (f"mention-{entity_id}", entity_id, f"ctx-{entity_id}"),
    )
    conn.execute(
        "INSERT INTO tag_assignments (tag_name, entity_id, assertion_id, assigned_by) "
        "VALUES ('primary', ?, ?, 'test')",
        (entity_id, assertion_id),
    )
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score) VALUES (?, 0.5)",
        (entity_id,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO session_edge_types (type, description, directional) "
        "VALUES ('continues', 'test', 1)"
    )
    conn.execute(
        "INSERT INTO session_edges (session_id, agent, from_node, to_node, edge_type) "
        "VALUES ('sess', 'test', ?, 'transcript:sess', 'continues')",
        (entity_id,),
    )
    conn.execute(
        "INSERT INTO session_journals (timestamp, agent, summary, entity_ids) "
        "VALUES ('2026-01-01T00:00:00Z', 'test', 's', ?)",
        (json.dumps([entity_id, peer_id]),),
    )
    conn.commit()
    return assertion_id


def test_rekey_full_surface_repoints_children_and_seeds_alias(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = "project:peer-a"
    _seed_entity(conn, peer)
    old_id = "project:old-full"
    assertion_id = _seed_full_surface(conn, old_id, peer_id=peer)
    new_id = "project:new-full"

    result = entity_rekey_impl(conn, old_id, new_id)
    assert result["new_id"] == new_id

    assert not conn.execute(
        "SELECT id FROM entities WHERE id = ?", (old_id,)
    ).fetchone()
    assert conn.execute(
        "SELECT id FROM entities WHERE id = ?", (new_id,)
    ).fetchone()

    row = conn.execute(
        "SELECT entrenchment_score FROM assertions WHERE id = ?", (assertion_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.42)

    for sql, params in [
        ("SELECT COUNT(*) FROM assertions WHERE entity_id = ?", (new_id,)),
        ("SELECT COUNT(*) FROM relationships WHERE from_entity = ?", (new_id,)),
        ("SELECT COUNT(*) FROM relationships WHERE to_entity = ?", (new_id,)),
        ("SELECT COUNT(*) FROM surface_forms WHERE entity_id = ?", (new_id,)),
        ("SELECT COUNT(*) FROM tag_assignments WHERE entity_id = ?", (new_id,)),
        ("SELECT COUNT(*) FROM session_edges WHERE from_node = ?", (new_id,)),
    ]:
        assert conn.execute(sql, params).fetchone()[0] >= 1

    resolved = resolve_entity_reference(conn, old_id, resolve_aliases=True)
    assert resolved.entity_id == new_id

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_entities.cortex_conn", lambda: conn
    )
    fetched = _op_entity_get(entity_id=old_id)
    assert fetched["id"] == new_id


def test_rekey_collision_new_id_exists_409(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "project:exists")
    _seed_entity(conn, "project:old-collision")
    with pytest.raises(HTTPException) as exc_info:
        entity_rekey_impl(conn, "project:old-collision", "project:exists")
    assert exc_info.value.status_code == 409


def test_rekey_collision_new_id_is_alias_409(conn: sqlite3.Connection) -> None:
    holder = "project:alias-holder"
    _seed_entity(conn, holder)
    _seed_entity(conn, "project:old-alias")
    conn.execute(
        "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, 'project', ?)",
        (holder, "project:taken-alias"),
    )
    conn.commit()
    with pytest.raises(HTTPException) as exc_info:
        entity_rekey_impl(conn, "project:old-alias", "project:taken-alias")
    assert exc_info.value.status_code == 409


def test_merge_dedup_collisions_and_tombstones_source(
    conn: sqlite3.Connection,
) -> None:
    target = "project:merge-target"
    source = "project:merge-source"
    _seed_entity(conn, target)
    _seed_entity(conn, source)
    claim = "shared claim"
    th = compute_claim_hash(target, claim)
    sh = compute_claim_hash(source, claim)
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash) VALUES (?, ?, 'believed', 'ev', 'inference', ?)",
        (target, claim, th),
    )
    target_assertion = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash) VALUES (?, ?, 'believed', 'ev', 'inference', ?)",
        (source, claim, sh),
    )
    source_assertion = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES (?, ?, 'evidence_for', 1)",
        (source, target),
    )
    conn.execute(
        "INSERT INTO tag_assignments (tag_name, entity_id, assertion_id, assigned_by) "
        "VALUES ('shared-tag', ?, ?, 'test'), ('shared-tag', ?, ?, 'test')",
        (target, target_assertion, source, source_assertion),
    )
    conn.commit()

    entity_merge_impl(conn, source, target)

    source_row = conn.execute(
        "SELECT lifecycle, attributes FROM entities WHERE id = ?", (source,)
    ).fetchone()
    assert source_row[0] == "merged"
    attrs = json.loads(source_row[1])
    assert attrs["merged_into"] == target

    superseded = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (source_assertion,)
    ).fetchone()[0]
    assert superseded == target_assertion

    tag_count = conn.execute(
        "SELECT COUNT(*) FROM tag_assignments WHERE tag_name = 'shared-tag' AND entity_id = ?",
        (target,),
    ).fetchone()[0]
    assert tag_count == 1

    self_loop = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE active = 1 "
        "AND from_entity = ? AND to_entity = ?",
        (target, target),
    ).fetchone()[0]
    assert self_loop == 0


def test_merge_redirect_and_raw_id_tombstone(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = "project:redirect-target"
    source = "project:redirect-source"
    _seed_entity(conn, target)
    _seed_entity(conn, source)
    entity_merge_impl(conn, source, target)

    resolved = resolve_entity_reference(conn, source, resolve_aliases=True)
    assert resolved.entity_id == target

    raw = resolve_entity_reference(conn, source, raw_id=True)
    assert raw.entity_id == source

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_entities.cortex_conn", lambda: conn
    )
    tombstone = _op_entity_get(entity_id=source, raw_id=True)
    assert tombstone["id"] == source
    assert tombstone["lifecycle"] == "merged"


def test_merge_self_loop_dropped(conn: sqlite3.Connection) -> None:
    target = "project:loop-target"
    source = "project:loop-source"
    _seed_entity(conn, target)
    _seed_entity(conn, source)
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES (?, ?, 'evidence_for', 1)",
        (source, target),
    )
    conn.commit()
    entity_merge_impl(conn, source, target)
    active_loops = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE active = 1 "
        "AND from_entity = ? AND to_entity = ?",
        (target, target),
    ).fetchone()[0]
    assert active_loops == 0


def test_registry_coverage_audit_passes(conn: sqlite3.Connection) -> None:
    errors = audit_entity_id_registry_coverage(conn)
    assert errors == []


def test_merge_cross_type_422(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "project:merge-proj", entity_type="project")
    _seed_entity(conn, "decision:merge-dec", entity_type="decision")
    with pytest.raises(HTTPException) as exc_info:
        entity_merge_impl(conn, "project:merge-proj", "decision:merge-dec")
    assert exc_info.value.status_code == 422


def test_atomicity_rolls_back_on_forced_failure(conn: sqlite3.Connection) -> None:
    _seed_entity(conn, "project:atomic-old")
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, derivation_type) "
        "VALUES ('project:atomic-old', 'x', 'believed', 'ev', 'inference')"
    )
    conn.commit()
    with patch(
        "cortex_store.entity_rekey_core.check_foreign_keys",
        side_effect=HTTPException(status_code=500, detail="forced"),
    ):
        with pytest.raises(HTTPException):
            entity_rekey_impl(conn, "project:atomic-old", "project:atomic-new")
    assert conn.execute(
        "SELECT id FROM entities WHERE id = 'project:atomic-old'"
    ).fetchone()
    assert not conn.execute(
        "SELECT id FROM entities WHERE id = 'project:atomic-new'"
    ).fetchone()
    check_foreign_keys(conn)


def test_resolver_passthrough_unchanged_for_normal_entity(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "project:passthrough"
    _seed_entity(conn, entity_id)
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_entities.cortex_conn", lambda: conn
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions.cortex_conn", lambda: conn
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_relationships.cortex_conn", lambda: conn
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_misc.cortex_conn", lambda: conn
    )

    resolved = resolve_entity_reference(conn, entity_id, resolve_aliases=True)
    assert resolved.entity_id == entity_id
    assert resolved.resolved_alias is None

    assert _op_entity_get(entity_id=entity_id)["id"] == entity_id
    assert _op_entity_update(entity_id=entity_id, notes="ok")["id"] == entity_id
    assert "error" not in _op_assertions(entity_id=entity_id)
    assert "error" not in _op_relationships(entity_id=entity_id)
    assert "error" not in _op_tag_list(entity_id=entity_id)
