"""S6 batch 3: dispatch vs typed parity for tag_resolve, rj_consolidate, deadline_resolve."""

from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.dispatch_ops import execute_op
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
_VOLATILE_ENTRY_KEYS = frozenset({"id", "created_at"})
_VOLATILE_ASSERTION_KEYS = frozenset(
    {"id", "observed_at", "created_at", "updated_at", "claim_hash", "quality_score"}
)
_VOLATILE_ENTITY_KEYS = frozenset({"created_at", "updated_at"})
_VOLATILE_DEADLINE_RESOLVE_KEYS = frozenset(
    {"resolution_assertion_id", "outcome_set"}
)
_RJ_ROW_COLS = (
    "agent",
    "register",
    "entry",
    "kind",
    "session_id",
    "revises",
    "consolidation_data",
)
_LINK_COLS = ("from_entry", "to_entry", "to_entity", "link_type")


def _without_dispatch_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


def _normalize_rj_body(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_dispatch_envelope(body)
    if not isinstance(body, dict):
        return body
    out = copy.deepcopy(body)
    for key in _VOLATILE_ENTRY_KEYS:
        out.pop(key, None)
    if isinstance(out.get("links"), list):
        for link in out["links"]:
            if isinstance(link, dict):
                link.pop("id", None)
                link.pop("created_at", None)
    if out.get("suggested_links") is None:
        out.pop("suggested_links", None)
    return out


def _normalize_tag_resolve(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_dispatch_envelope(body)
    out = copy.deepcopy(body)
    if isinstance(out.get("assertion"), dict):
        out["assertion"] = {
            k: v
            for k, v in out["assertion"].items()
            if k not in _VOLATILE_ASSERTION_KEYS
        }
    if isinstance(out.get("entity"), dict):
        out["entity"] = {
            k: v for k, v in out["entity"].items() if k not in _VOLATILE_ENTITY_KEYS
        }
    return out


def _normalize_deadline_resolve(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_dispatch_envelope(body)
    return {k: v for k, v in body.items() if k not in _VOLATILE_DEADLINE_RESOLVE_KEYS}


def _isolated_client(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
) -> TestClient:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    return TestClient(create_app(db_path=str(db_path)))


def _seed_tag_fixture(conn: sqlite3.Connection, *, entity_id: str, assertion_id: int) -> None:
    insert_entity(conn, entity_id=entity_id, entity_type="decision")
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, derivation_type, observed_at) "
        "VALUES (?, ?, 'believed', 'agent_observation', datetime('now'))",
        (entity_id, "batch3 tag seed claim"),
    )
    row = conn.execute(
        "SELECT id FROM assertions WHERE entity_id = ? ORDER BY id DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    assert row is not None
    aid = int(row[0])
    conn.execute(
        "INSERT INTO tag_assignments (tag_name, entity_id, assertion_id, assigned_by) "
        "VALUES ('current', ?, ?, 'pytest')",
        (entity_id, aid),
    )
    conn.commit()


def _seed_rj_source_entries(conn: sqlite3.Connection) -> tuple[int, int]:
    cur = conn.execute(
        "INSERT INTO reflective_journal (agent, register, entry, kind) "
        "VALUES ('cursor-sdk', 'analyst', 'source entry one', 'entry')"
    )
    e1 = int(cur.lastrowid)
    cur = conn.execute(
        "INSERT INTO reflective_journal (agent, register, entry, kind) "
        "VALUES ('cursor-sdk', 'analyst', 'source entry two', 'entry')"
    )
    e2 = int(cur.lastrowid)
    conn.commit()
    return e1, e2


def _rj_consolidate_args(*, source_ids: list[int]) -> dict[str, Any]:
    return {
        "agent": "cursor-sdk",
        "register": "analyst",
        "entry": "S6 batch 3 consolidation parity body.",
        "session_id": "s6-batch3-parity",
        "throughline": "parity throughline",
        "before": "before state",
        "now": "now state",
        "tension_points": ["t1", "t2"],
        "contradiction_set": ["c1"],
        "falsifier": "would falsify if X",
        "rendered_shift": "shift narrative",
        "confidence": "believed",
        "source_entry_ids": source_ids,
    }


def _fetch_rj_row(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT {', '.join(_RJ_ROW_COLS)} FROM reflective_journal WHERE id = ?",
        (entry_id,),
    ).fetchone()
    assert row is not None
    data = dict(zip(_RJ_ROW_COLS, row, strict=True))
    if data.get("consolidation_data"):
        data["consolidation_data"] = json.loads(data["consolidation_data"])
    return data


def _fetch_links_for_entry(conn: sqlite3.Connection, entry_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {', '.join(_LINK_COLS)} FROM journal_links WHERE from_entry = ? ORDER BY to_entry",
        (entry_id,),
    ).fetchall()
    return [dict(zip(_LINK_COLS, r, strict=True)) for r in rows]


def _seed_deadline(conn: sqlite3.Connection, *, deadline_id: str = "deadline:s6-batch3") -> None:
    insert_entity(
        conn,
        entity_id=deadline_id,
        entity_type="deadline",
        name="Batch 3 parity deadline",
        attributes=json.dumps({"deadline_date": "2026-12-01", "urgency": "high"}),
    )
    conn.commit()


def _deadline_attrs(conn: sqlite3.Connection, deadline_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT attributes FROM entities WHERE id = ?",
        (deadline_id,),
    ).fetchone()
    assert row is not None
    raw = row[0]
    return json.loads(raw) if isinstance(raw, str) and raw else {}


def _resolved_assertion_count(conn: sqlite3.Connection, deadline_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM assertions WHERE entity_id = ? AND confidence = 'confirmed' "
        "AND UPPER(claim) LIKE '%RESOLVED%'",
        (deadline_id,),
    ).fetchone()
    return int(row[0]) if row else 0


@pytest.mark.offline
def test_tag_resolve_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "decision:s6-batch3-tag"

    def seed(conn: sqlite3.Connection) -> None:
        _seed_tag_fixture(conn, entity_id=entity_id, assertion_id=0)

    args = {"tag_name": "current", "entity_id": entity_id}
    bind_db = tmp_path / "cortex_dispatch_tag.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    seed(cortex_db.cortex_conn())
    dispatch_body = _normalize_tag_resolve(execute_op("tag_resolve", args))

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_tag"
    )
    seed(cortex_db.cortex_conn())
    resp = typed_client.get(
        "/tags/current/resolve",
        params={"entity_id": entity_id},
    )
    assert resp.status_code == 200, resp.text
    assert dispatch_body == _normalize_tag_resolve(resp.json())

    with pytest.raises(HTTPException) as dispatch_exc:
        execute_op("tag_resolve", {"tag_name": "missing", "entity_id": entity_id})
    missing_resp = typed_client.get(
        "/tags/missing/resolve",
        params={"entity_id": entity_id},
    )
    assert missing_resp.status_code == dispatch_exc.value.status_code
    assert missing_resp.json() == {"detail": dispatch_exc.value.detail}


@pytest.mark.offline
def test_rj_consolidate_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def seed(conn: sqlite3.Connection) -> tuple[int, int]:
        return _seed_rj_source_entries(conn)

    bind_db = tmp_path / "cortex_dispatch_rj.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    e1, e2 = seed(cortex_db.cortex_conn())
    args = _rj_consolidate_args(source_ids=[e1, e2])
    dispatch_raw = execute_op("rj_consolidate", args)
    assert "error" not in dispatch_raw, dispatch_raw
    dispatch_body = _normalize_rj_body(dispatch_raw)
    dispatch_entry_id = dispatch_raw["id"]
    dispatch_row = _fetch_rj_row(cortex_db.cortex_conn(), dispatch_entry_id)
    dispatch_links = _fetch_links_for_entry(cortex_db.cortex_conn(), dispatch_entry_id)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_rj"
    )
    e1t, e2t = seed(cortex_db.cortex_conn())
    assert (e1, e2) == (e1t, e2t)
    http_resp = typed_client.post("/reflective-journal/consolidations", json=args)
    assert http_resp.status_code == 200, http_resp.text
    typed_raw = http_resp.json()
    typed_body = _normalize_rj_body(typed_raw)
    assert dispatch_body == typed_body
    typed_entry_id = typed_raw["id"]
    typed_row = _fetch_rj_row(cortex_db.cortex_conn(), typed_entry_id)
    typed_links = _fetch_links_for_entry(cortex_db.cortex_conn(), typed_entry_id)
    assert dispatch_row == typed_row
    assert dispatch_links == typed_links


@pytest.mark.offline
def test_deadline_resolve_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline_id = "deadline:s6-batch3"
    body = {
        "resolution_note": "batch 3 parity resolution",
        "resolved_at": "2026-10-05T12:00:00Z",
        "evidence": "parity evidence text",
        "outcome": "met",
    }
    dispatch_args = {"deadline_id": deadline_id, **body}

    bind_db = tmp_path / "cortex_dispatch_dl.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _seed_deadline(cortex_db.cortex_conn(), deadline_id=deadline_id)
    dispatch_raw = execute_op("deadline_resolve", dispatch_args)
    assert "error" not in dispatch_raw, dispatch_raw
    dispatch_body = _normalize_deadline_resolve(dispatch_raw)
    dispatch_attrs = _deadline_attrs(cortex_db.cortex_conn(), deadline_id)
    dispatch_assertions = _resolved_assertion_count(cortex_db.cortex_conn(), deadline_id)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_dl"
    )
    _seed_deadline(cortex_db.cortex_conn(), deadline_id=deadline_id)
    http_resp = typed_client.post(f"/deadlines/{deadline_id}/resolve", json=body)
    assert http_resp.status_code == 200, http_resp.text
    typed_body = _normalize_deadline_resolve(http_resp.json())
    assert dispatch_body == typed_body
    typed_attrs = _deadline_attrs(cortex_db.cortex_conn(), deadline_id)
    typed_assertions = _resolved_assertion_count(cortex_db.cortex_conn(), deadline_id)
    assert dispatch_attrs.get("outcome") == typed_attrs.get("outcome") == "met"
    assert dispatch_assertions == typed_assertions == 1


@pytest.mark.offline
def test_rj_consolidate_typed_route_atomic_on_link_failure(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry insert + links share one commit (reflective_journal.py:309-319)."""
    from cortex_store.routes import reflective_journal as rj_mod

    real_insert = rj_mod._insert_reflective_entry_tx

    def fail_after_entry_insert(*args: Any, **kwargs: Any) -> int:
        entry_id = real_insert(*args, **kwargs)
        raise RuntimeError("injected after entry insert")

    monkeypatch.setattr(rj_mod, "_insert_reflective_entry_tx", fail_after_entry_insert)

    db_path = tmp_path / "cortex_rj_atomic.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    client = TestClient(
        create_app(db_path=str(db_path)), raise_server_exceptions=False
    )
    e1, _e2 = _seed_rj_source_entries(cortex_db.cortex_conn())
    args = _rj_consolidate_args(source_ids=[e1])
    resp = client.post("/reflective-journal/consolidations", json=args)
    assert resp.status_code >= 500

    count = cortex_db.cortex_conn().execute(
        "SELECT COUNT(*) FROM reflective_journal WHERE kind = 'consolidation'"
    ).fetchone()[0]
    link_count = cortex_db.cortex_conn().execute(
        "SELECT COUNT(*) FROM journal_links"
    ).fetchone()[0]
    assert int(count) == 0
    assert int(link_count) == 0


@pytest.mark.offline
def test_deadline_resolve_typed_route_atomic_on_outcome_failure(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cortex_store.dispatch_ops.ops_journals as dj_mod

    original_execute = dj_mod.execute

    def execute_guard(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
        if "UPDATE entities SET attributes" in sql:
            raise sqlite3.OperationalError("injected outcome update failure")
        return original_execute(conn, sql, params)

    monkeypatch.setattr(dj_mod, "execute", execute_guard)

    deadline_id = "deadline:s6-batch3-atomic"
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="dl_atomic"
    )
    _seed_deadline(cortex_db.cortex_conn(), deadline_id=deadline_id)
    body = {
        "resolution_note": "atomic failure probe",
        "resolved_at": "2026-10-05T12:00:00Z",
        "outcome": "met",
    }
    resp = client.post(f"/deadlines/{deadline_id}/resolve", json=body)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("outcome_set") is False

    attrs = _deadline_attrs(cortex_db.cortex_conn(), deadline_id)
    assert attrs.get("outcome") != "met"
    assert _resolved_assertion_count(cortex_db.cortex_conn(), deadline_id) == 0


@pytest.mark.offline
def test_batch3_paths_do_not_shadow_existing_routes(
    cortex_client: TestClient,
) -> None:
    tags_list = cortex_client.get("/tags", params={"entity_id": "decision:shadow-probe"})
    assert tags_list.status_code == 200

    deadlines_list = cortex_client.get("/deadlines")
    assert deadlines_list.status_code == 200

    rj_list = cortex_client.get("/reflective-journal")
    assert rj_list.status_code == 200

    rj_read = cortex_client.get("/reflective-journal/1")
    assert rj_read.status_code != 422 or "int_parsing" not in rj_read.text

    observe = cortex_client.post(
        "/assertions/observations",
        json={"entity_id": "decision:shadow", "claim": "x", "agent": "pytest"},
    )
    assert observe.status_code != 422 or "int_parsing" not in observe.text

    frictions = cortex_client.get("/frictions")
    assert frictions.status_code == 200
