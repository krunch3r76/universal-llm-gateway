"""W6 live entity-state deadline closure (spec cortex-live-entity-state-w6-deadlines-entity-state)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import pytest

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.entity_state_fold import fold_claim, reverse_fold
from cortex_store.entity_state_read import _field_verified
from cortex_store.routes.deadlines import (
    _head_closes,
    _list_deadlines_impl,
    _resolved_assertion_ids,
    list_deadlines,
)


@pytest.fixture(autouse=True)
def _bind_list_deadlines_db(
    migrated_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = migrated_conn.execute("PRAGMA database_list").fetchone()
    bind_cortex_db(monkeypatch, Path(str(row[2])))


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future_date() -> str:
    return "2099-06-01"


def _insert_status_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    evidence_uris: str = '["cortex://notes/evidence.md"]',
    token: str = "submitted",
) -> int:
    ts = _now()
    pf = f"status({entity_id}, {token}, current)"
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "derivation_type, evidence_uris, entrenchment_score, created_at, updated_at, observed_at) "
        "VALUES (?, ?, 'confirmed', ?, 'direct_observation', ?, 0.5, ?, ?, ?)",
        (entity_id, "status claim", pf, evidence_uris, ts, ts, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def _seed_deadline_pair(
    conn: sqlite3.Connection,
    *,
    subject_id: str = "account:w6-subject",
    deadline_id: str = "deadline:w6",
    edge_active: int = 1,
    deadline_date: str | None = None,
    subject_attrs: dict[str, Any] | None = None,
    deadline_attrs: dict[str, Any] | None = None,
    deadline_wf: str | None = None,
    subject_type: str = "account",
) -> tuple[str, str]:
    insert_entity(
        conn,
        entity_id=subject_id,
        entity_type=subject_type,
        name="W6 subject",
        attributes=json.dumps(subject_attrs or {}),
    )
    d_attrs = {"deadline_date": deadline_date or _future_date(), **(deadline_attrs or {})}
    insert_entity(
        conn,
        entity_id=deadline_id,
        entity_type="deadline",
        name="W6 deadline",
        workflow_state=deadline_wf,
        attributes=json.dumps(d_attrs),
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) VALUES (?, ?, 'deadline_for', ?)",
        (deadline_id, subject_id, edge_active),
    )
    conn.commit()
    return subject_id, deadline_id


def _listed_ids(*, include_resolved: bool = False) -> set[str]:
    return {
        str(i["deadline_id"])
        for i in _list_deadlines_impl(include_resolved=include_resolved)["items"]
    }


def _resolved_row(deadline_id: str) -> dict[str, Any] | None:
    rows = _list_deadlines_impl(include_resolved=True)["items"]
    for row in rows:
        if row.get("deadline_id") == deadline_id:
            return row
    return None


# --- parity / unit helpers ---


def test_w6_head_closes_matches_field_verified(migrated_conn: sqlite3.Connection) -> None:
    dup_attrs = {
        "state_provenance": {
            "application_submitted": [
                {
                    "provenance_id": "application_submitted:1",
                    "kind": "fold",
                    "ref": "a:1",
                    "ref_kind": "assertion",
                    "value": True,
                    "assertion_id": 1,
                },
                {
                    "provenance_id": "application_submitted:1",
                    "kind": "fold",
                    "ref": "a:2",
                    "ref_kind": "assertion",
                    "value": True,
                    "assertion_id": 2,
                },
            ]
        },
        "application_submitted": True,
    }
    verified, _ = _field_verified(dup_attrs, "application_submitted", None)
    assert verified == "unverified"
    assert not _head_closes(dup_attrs, "application_submitted", True)

    op_attrs = {
        "state_provenance": {
            "application_submitted": [
                {
                    "provenance_id": "application_submitted:1",
                    "kind": "operator_write",
                    "ref": "op",
                    "ref_kind": "operator",
                    "value": True,
                }
            ]
        },
        "application_submitted": True,
    }
    verified_op, _ = _field_verified(op_attrs, "application_submitted", None)
    assert verified_op == "verified"
    assert not _head_closes(op_attrs, "application_submitted", True)

    fold_attrs = {
        "state_provenance": {
            "application_submitted": [
                {
                    "provenance_id": "application_submitted:1",
                    "kind": "fold",
                    "ref": "a:1",
                    "ref_kind": "assertion",
                    "value": True,
                    "assertion_id": 1,
                }
            ]
        },
        "application_submitted": True,
    }
    verified_fold, _ = _field_verified(fold_attrs, "application_submitted", None)
    assert verified_fold == "verified"
    assert _head_closes(fold_attrs, "application_submitted", True)


# --- legacy + claims ---


def test_legacy_outcome_without_provenance_stays_closed(migrated_conn: sqlite3.Connection) -> None:
    _, deadline_id = _seed_deadline_pair(
        migrated_conn,
        deadline_attrs={"outcome": "met"},
    )
    assert deadline_id not in _listed_ids()
    row = _resolved_row(deadline_id)
    assert row is not None
    assert row["resolved_via"] == ["outcome"]
    assert row["resolved_by"] == []


def test_unresolved_claim_does_not_close(migrated_conn: sqlite3.Connection) -> None:
    _, deadline_id = _seed_deadline_pair(migrated_conn)
    conn = migrated_conn
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, superseded_by, created_at) "
        "VALUES (?, 'Item UNRESOLVED pending', 'confirmed', NULL, ?)",
        (deadline_id, _now()),
    )
    conn.commit()
    assert deadline_id in _listed_ids()


def test_resolved_claim_closes_and_cites_assertion_id(migrated_conn: sqlite3.Connection) -> None:
    _, deadline_id = _seed_deadline_pair(migrated_conn)
    cur = migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, superseded_by, created_at) "
        "VALUES (?, 'Deadline RESOLVED on time', 'confirmed', NULL, ?)",
        (deadline_id, _now()),
    )
    migrated_conn.commit()
    aid = int(cur.lastrowid)
    assert deadline_id not in _listed_ids()
    row = _resolved_row(deadline_id)
    assert row is not None
    assert "resolved_claim" in row["resolved_via"]
    assert aid in row["resolved_by"]


# --- fold closure paths ---


def test_workflow_state_head_closes_deadline(migrated_conn: sqlite3.Connection) -> None:
    subject_id, deadline_id = _seed_deadline_pair(migrated_conn, deadline_wf="open")
    aid = _insert_status_assertion(
        migrated_conn, entity_id=deadline_id, token="done", evidence_uris='["cortex://notes/e.md"]'
    )
    migrated_conn.execute(
        "UPDATE assertions SET predicate_form = ? WHERE id = ?",
        (f"status({deadline_id}, done, current)", aid),
    )
    migrated_conn.commit()
    fold_claim(migrated_conn, deadline_id, aid, "workflow_state")
    migrated_conn.execute(
        "UPDATE entities SET workflow_state = 'done' WHERE id = ?", (deadline_id,)
    )
    migrated_conn.commit()
    assert deadline_id not in _listed_ids()
    row = _resolved_row(deadline_id)
    assert row is not None
    assert row["resolved_via"] == ["fold"]
    assert row["resolved_by"] == [aid]


def test_application_submitted_head_on_subject_closes_deadline(
    migrated_conn: sqlite3.Connection,
) -> None:
    subject_id, deadline_id = _seed_deadline_pair(migrated_conn)
    aid = _insert_status_assertion(migrated_conn, entity_id=subject_id)
    fold_claim(migrated_conn, subject_id, aid, "application_submitted")
    assert deadline_id not in _listed_ids()
    row = _resolved_row(deadline_id)
    assert row is not None
    assert row["resolved_via"] == ["fold"]
    assert row["resolved_by"] == [aid]


def test_fold_claim_fixture_default_list_omits_include_resolved_cites_fold(
    migrated_conn: sqlite3.Connection,
) -> None:
    subject_id, deadline_id = _seed_deadline_pair(migrated_conn, subject_type="account")
    aid = _insert_status_assertion(migrated_conn, entity_id=subject_id)
    fold_claim(migrated_conn, subject_id, aid, "application_submitted")
    assert deadline_id not in _listed_ids()
    resolved = _list_deadlines_impl(include_resolved=True)["items"]
    hit = next(i for i in resolved if i["deadline_id"] == deadline_id)
    assert hit["resolved_by"] == [aid]
    assert hit["resolved_via"] == ["fold"]


def test_reversed_fold_reopens_deadline(migrated_conn: sqlite3.Connection) -> None:
    subject_id, deadline_id = _seed_deadline_pair(migrated_conn)
    aid = _insert_status_assertion(migrated_conn, entity_id=subject_id)
    pid = fold_claim(migrated_conn, subject_id, aid, "application_submitted").provenance_id or ""
    assert deadline_id not in _listed_ids()
    reverse_fold(migrated_conn, subject_id, "application_submitted", pid)
    assert deadline_id in _listed_ids()


def test_application_submitted_head_on_deadline_entity_does_not_close(
    migrated_conn: sqlite3.Connection,
) -> None:
    subject_id, deadline_id = _seed_deadline_pair(migrated_conn)
    aid = _insert_status_assertion(migrated_conn, entity_id=deadline_id)
    fold_claim(migrated_conn, deadline_id, aid, "application_submitted")
    assert deadline_id in _listed_ids()


def test_application_submitted_head_on_unrelated_entity_does_not_close(
    migrated_conn: sqlite3.Connection,
) -> None:
    _, deadline_id = _seed_deadline_pair(migrated_conn)
    other = "account:w6-other"
    insert_entity(migrated_conn, entity_id=other, entity_type="account", name="other")
    aid = _insert_status_assertion(migrated_conn, entity_id=other)
    fold_claim(migrated_conn, other, aid, "application_submitted")
    assert deadline_id in _listed_ids()


def test_application_submitted_head_via_inactive_edge_does_not_close(
    migrated_conn: sqlite3.Connection,
) -> None:
    subject_id, deadline_id = _seed_deadline_pair(migrated_conn, edge_active=0)
    aid = _insert_status_assertion(migrated_conn, entity_id=subject_id)
    fold_claim(migrated_conn, subject_id, aid, "application_submitted")
    assert deadline_id in _listed_ids()


def test_fulfillment_claim_without_fold_stays_open(migrated_conn: sqlite3.Connection) -> None:
    subject_id, deadline_id = _seed_deadline_pair(migrated_conn)
    _insert_status_assertion(migrated_conn, entity_id=subject_id)
    assert deadline_id in _listed_ids()


def test_forged_state_provenance_does_not_close(migrated_conn: sqlite3.Connection) -> None:
    forged = {
        "state_provenance": {
            "application_submitted": [
                {
                    "provenance_id": "application_submitted:1",
                    "kind": "fold",
                    "value": True,
                    "assertion_id": 1,
                }
            ]
        },
        "application_submitted": False,
    }
    _, deadline_id = _seed_deadline_pair(migrated_conn, subject_attrs=forged)
    assert deadline_id in _listed_ids()


def test_forged_workflow_state_head_with_null_value_does_not_close(
    migrated_conn: sqlite3.Connection,
) -> None:
    forged = {
        "state_provenance": {
            "workflow_state": [
                {
                    "provenance_id": "workflow_state:1",
                    "kind": "fold",
                    "ref": "a:1",
                    "ref_kind": "assertion",
                    "value": None,
                    "assertion_id": 1,
                }
            ]
        }
    }
    _, deadline_id = _seed_deadline_pair(
        migrated_conn, deadline_wf=None, deadline_attrs=forged
    )
    assert deadline_id in _listed_ids()


def test_fold_head_without_assertion_id_closes_but_omits_from_resolved_by(
    migrated_conn: sqlite3.Connection,
) -> None:
    attrs = {
        "application_submitted": True,
        "state_provenance": {
            "application_submitted": [
                {
                    "provenance_id": "application_submitted:1",
                    "kind": "fold",
                    "ref": "a:1",
                    "ref_kind": "assertion",
                    "value": True,
                }
            ]
        },
    }
    _, deadline_id = _seed_deadline_pair(migrated_conn, subject_attrs=attrs)
    assert deadline_id not in _listed_ids()
    row = _resolved_row(deadline_id)
    assert row is not None
    assert row["resolved_via"] == ["fold"]
    assert row["resolved_by"] == []


# --- todo arm ---


def test_todo_legacy_outcome_met_is_omitted(migrated_conn: sqlite3.Connection) -> None:
    eid = "todo:w6-outcome"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="todo outcome",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": _future_date(), "outcome": "met"}),
    )
    assert eid not in _listed_ids()


def test_todo_application_submitted_head_on_todo_closes(migrated_conn: sqlite3.Connection) -> None:
    eid = "todo:w6-fold-self"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="todo fold",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": _future_date()}),
    )
    aid = _insert_status_assertion(migrated_conn, entity_id=eid)
    fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert eid not in _listed_ids()


# --- batch / infra ---


def test_resolved_assertion_ids_chunks_large_batches(migrated_conn: sqlite3.Connection) -> None:
    ids: list[str] = []
    for i in range(520):
        dl = f"deadline:chunk-{i}"
        ids.append(dl)
        insert_entity(
            migrated_conn,
            entity_id=dl,
            entity_type="deadline",
            name=dl,
            attributes=json.dumps({"deadline_date": _future_date()}),
        )
        migrated_conn.execute(
            "INSERT INTO assertions (entity_id, claim, confidence, created_at) "
            "VALUES (?, 'RESOLVED chunk', 'confirmed', ?)",
            (dl, _now()),
        )
    migrated_conn.commit()
    single = _resolved_assertion_ids(migrated_conn, ids)
    assert len(single) == 520
    assert all(single[k] for k in single)


def test_include_resolved_returns_resolved_by_and_resolved_via(
    migrated_conn: sqlite3.Connection,
) -> None:
    _, deadline_id = _seed_deadline_pair(migrated_conn, deadline_attrs={"outcome": "met"})
    open_only = _list_deadlines_impl(include_resolved=False)["items"]
    assert deadline_id not in {i["deadline_id"] for i in open_only}
    all_rows = _list_deadlines_impl(include_resolved=True)["items"]
    hit = next(i for i in all_rows if i["deadline_id"] == deadline_id)
    assert hit["resolved_via"]
    assert "resolved_by" in hit


def test_list_deadlines_query_count_is_constant(
    migrated_db_path: Path,
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_db = tmp_path / "w6_qcount.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    conn = sqlite3.connect(bind_db)
    conn.row_factory = sqlite3.Row
    for i in range(20):
        insert_entity(
            conn,
            entity_id=f"todo:q{i}",
            entity_type="todo",
            name=f"todo {i}",
            workflow_state="open",
            attributes=json.dumps({"deadline_date": _future_date()}),
        )
    conn.commit()
    counts: list[int] = []

    def _trace(sql: str) -> None:
        if sql.strip().upper().startswith("SELECT"):
            counts.append(1)

    def _run() -> int:
        counts.clear()
        conn.set_trace_callback(_trace)
        bind_cortex_db(monkeypatch, bind_db)
        _list_deadlines_impl(include_resolved=False)
        conn.set_trace_callback(None)
        return len(counts)

    conn.execute("DELETE FROM entities WHERE id LIKE 'todo:q%'").connection.commit()
    insert_entity(
        conn,
        entity_id="todo:q-one",
        entity_type="todo",
        name="one",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": _future_date()}),
    )
    conn.commit()
    one = _run()
    for i in range(19):
        insert_entity(
            conn,
            entity_id=f"todo:q-extra-{i}",
            entity_type="todo",
            name=f"x{i}",
            workflow_state="open",
            attributes=json.dumps({"deadline_date": _future_date()}),
        )
    conn.commit()
    twenty = _run()
    assert one == twenty


def test_list_deadlines_does_not_write(migrated_conn: sqlite3.Connection) -> None:
    _seed_deadline_pair(migrated_conn)
    entities_before = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assertions_before = migrated_conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    _list_deadlines_impl(include_resolved=False)
    entities_after = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assertions_after = migrated_conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert entities_after == entities_before
    assert assertions_after == assertions_before


def test_list_deadlines_operational_error_raises_without_partial_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> sqlite3.Connection:
        raise RuntimeError("db down")

    monkeypatch.setattr("cortex_store.routes.deadlines.cortex_conn", _boom)
    with pytest.raises(RuntimeError, match="db down"):
        list_deadlines(include_resolved=False)


def test_application_submitted_subject_head_closes_all_that_subjects_deadlines(
    migrated_conn: sqlite3.Connection,
) -> None:
    subject_id = "account:w6-multi"
    d1 = "deadline:w6-a"
    d2 = "deadline:w6-b"
    _seed_deadline_pair(migrated_conn, subject_id=subject_id, deadline_id=d1)
    insert_entity(
        migrated_conn,
        entity_id=d2,
        entity_type="deadline",
        name="second",
        attributes=json.dumps({"deadline_date": _future_date()}),
    )
    migrated_conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) VALUES (?, ?, 'deadline_for', 1)",
        (d2, subject_id),
    )
    migrated_conn.commit()
    aid = _insert_status_assertion(migrated_conn, entity_id=subject_id)
    fold_claim(migrated_conn, subject_id, aid, "application_submitted")
    listed = _listed_ids()
    assert d1 not in listed
    assert d2 not in listed
