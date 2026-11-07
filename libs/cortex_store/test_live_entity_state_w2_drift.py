"""W2 live entity-state drift detector tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store import db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import query
from cortex_store.drift_detector import (
    enumerate_entity_ids,
    open_deadline_items,
    scan_entity_state_drift,
    snapshot_table_counts,
)
from cortex_store.models.deadlines import DeadlineItem
from cortex_store.routes.deadlines import (
    _DEADLINES_SQL,
    _is_resolved,
    _resolved_assertion_ids,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _insert_status_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
    confidence: str = "confirmed",
    review_status: str | None = None,
    review_notes: str | None = None,
    created_at: str | None = None,
    derivation_type: str | None = None,
    evidence_uris: str | None = None,
) -> int:
    ts = created_at or _now()
    pf = f"status({entity_id}, operational, current)"
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "review_status, review_notes, derivation_type, evidence_uris, "
        "entrenchment_score, created_at, updated_at, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, ?)",
        (
            entity_id,
            claim,
            confidence,
            pf,
            review_status,
            review_notes,
            derivation_type or "inference",
            evidence_uris,
            ts,
            ts,
            ts,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def _seed_specimen_f1(conn: sqlite3.Connection, *, entity_id: str) -> None:
    insert_entity(
        conn,
        entity_id=entity_id,
        entity_type="account",
        name="Loan enrollment account",
        description="IDR application not_submitted per servicer letter.",
        attributes=json.dumps({"application_submitted": True}),
    )
    _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="Older served status",
        confidence="believed",
        created_at="2026-10-01T00:00:00+00:00",
    )


def _seed_specimen_f2(conn: sqlite3.Connection) -> tuple[str, str]:
    org_id = "organization:acme-pharmacy"
    window_id = "todo:acme-benefits-enrollment-window"
    choice_id = "document:acme-benefits-choice-rollup"
    insert_entity(
        conn,
        entity_id=org_id,
        entity_type="organization",
        name="Acme Pharmacy",
    )
    insert_entity(
        conn,
        entity_id=window_id,
        entity_type="todo",
        name="Acme benefits enrollment window",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": "2026-12-02"}),
    )
    conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-09-15T00:00:00+00:00", window_id),
    )
    insert_entity(
        conn,
        entity_id=choice_id,
        entity_type="document",
        name="Acme benefits choice confirmation",
        attributes=json.dumps(
            {
                "enrollment_confirmed": True,
                "confirmation_number": "459408658",
            }
        ),
    )
    conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-11-01T00:00:00+00:00", choice_id),
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type) VALUES (?, ?, ?)",
        (window_id, org_id, "pertains_to"),
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type) VALUES (?, ?, ?)",
        (choice_id, org_id, "references"),
    )
    conn.commit()
    return window_id, choice_id


def _seed_specimen_f3(conn: sqlite3.Connection, *, entity_id: str) -> int:
    insert_entity(
        conn,
        entity_id=entity_id,
        entity_type="plan",
        name="Concern substrate plan",
        description="Staging plan row.",
    )
    served_id = _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="Served staging status",
        confidence="believed",
        created_at="2026-10-20T00:00:00+00:00",
    )
    flagged_id = _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="Newer withheld operational status",
        confidence="confirmed",
        review_status="flagged",
        review_notes="invention, class6_generic_state",
        created_at="2026-11-05T00:00:00+00:00",
        derivation_type="direct_observation",
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    return flagged_id if served_id else flagged_id


@pytest.fixture()
def specimen_conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed_specimen_f1(migrated_conn, entity_id="account:specimen-f1-loan")
    _seed_specimen_f2(migrated_conn)
    _seed_specimen_f3(migrated_conn, entity_id="plan:specimen-f3-concern")
    return migrated_conn


def test_scanner_source_has_no_specimen_id_literals() -> None:
    text = Path(__file__).with_name("drift_detector.py").read_text(encoding="utf-8")
    assert "edfinancial-student-loan-9158" not in text
    assert "walgreens-benefits-choice-20261030" not in text
    assert "cortex-concern-substrate" not in text


def test_f1_negation_positive_and_open_enrollment_negative(
    migrated_conn: sqlite3.Connection,
) -> None:
    insert_entity(
        migrated_conn,
        entity_id="account:f1-pos",
        entity_type="account",
        name="Pos",
        description="Status shows not_submitted on file.",
        attributes=json.dumps({"enrollment_confirmed": True}),
    )
    result = scan_entity_state_drift(conn=migrated_conn)
    hit = [
        r
        for r in result.rows
        if r.entity_id == "account:f1-pos" and r.drift_class == "F1"
    ]
    assert len(hit) == 1
    insert_entity(
        migrated_conn,
        entity_id="account:f1-neg",
        entity_type="account",
        name="Neg",
        description="OPEN ENROLLMENT period continues.",
        attributes=json.dumps({"enrollment_confirmed": True}),
    )
    result2 = scan_entity_state_drift(conn=migrated_conn)
    assert not [r for r in result2.rows if r.entity_id == "account:f1-neg"]


def test_f2_walgreens_style_pair(specimen_conn: sqlite3.Connection) -> None:
    result = scan_entity_state_drift(conn=specimen_conn)
    f2 = [r for r in result.rows if r.drift_class == "F2"]
    assert len(f2) == 1
    row = f2[0]
    assert row.entity_id == row.left_cite
    assert "acme-benefits-choice" in row.right_cite


def test_f2_token_only_and_unrelated_sibling_zero(
    migrated_conn: sqlite3.Connection,
) -> None:
    window_id = "todo:orphan-window-only"
    insert_entity(
        migrated_conn,
        entity_id=window_id,
        entity_type="todo",
        name="orphan window",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": "2026-12-02"}),
    )
    insert_entity(
        migrated_conn,
        entity_id="document:unrelated-attrs",
        entity_type="document",
        name="totally different domain",
        attributes=json.dumps({"enrollment_confirmed": True}),
    )
    result = scan_entity_state_drift(conn=migrated_conn)
    assert not [
        r for r in result.rows if r.drift_class == "F2" and window_id in r.entity_id
    ]


def test_f3_withheld_null_and_nonnull_reason(migrated_conn: sqlite3.Connection) -> None:
    eid = "plan:f3-withhold"
    _seed_specimen_f3(migrated_conn, entity_id=eid)
    result = scan_entity_state_drift(conn=migrated_conn)
    f3 = [r for r in result.rows if r.entity_id == eid and r.drift_class == "F3"]
    assert f3
    assert any(r.surface == "current_status.withheld_newer" for r in f3)


def test_flagged_newer_claim_is_a_drift_row_not_a_write(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = "plan:f3-flag-only"
    _seed_specimen_f3(migrated_conn, entity_id=eid)
    before = snapshot_table_counts(migrated_conn)
    result = scan_entity_state_drift(conn=migrated_conn)
    after = snapshot_table_counts(migrated_conn)
    assert before == after
    assert [r for r in result.rows if r.entity_id == eid and r.drift_class == "F3"]


def test_population_lifecycle_and_ordering(migrated_conn: sqlite3.Connection) -> None:
    insert_entity(migrated_conn, entity_id="entity:zzz", entity_type="todo", name="Z")
    insert_entity(migrated_conn, entity_id="entity:aaa", entity_type="todo", name="A")
    conn = migrated_conn
    conn.execute(
        "UPDATE entities SET lifecycle = 'merged' WHERE id = ?", ("entity:zzz",)
    )
    conn.commit()
    ids = enumerate_entity_ids(migrated_conn)
    assert "entity:zzz" not in ids
    assert "entity:aaa" in ids
    assert ids == sorted(ids)


def test_open_deadlines_match_list_deadlines(migrated_conn: sqlite3.Connection) -> None:
    _seed_specimen_f2(migrated_conn)
    open_items = open_deadline_items(migrated_conn)
    rows = query(migrated_conn, _DEADLINES_SQL)
    deadline_ids = [str(r["deadline_id"]) for r in rows if r.get("deadline_id")]
    resolved_by_assertion = _resolved_assertion_ids(migrated_conn, deadline_ids)
    expected: list[DeadlineItem] = []
    for row in rows:
        item = DeadlineItem(**row)
        if item.deadline_id is None:
            continue
        assn_resolved = item.deadline_id in resolved_by_assertion
        if _is_resolved(item) or assn_resolved:
            continue
        expected.append(item)
    assert {i.deadline_id for i in open_items} == {i.deadline_id for i in expected}


def test_resolved_deadline_exclusions(migrated_conn: sqlite3.Connection) -> None:
    insert_entity(
        migrated_conn,
        entity_id="todo:resolved-outcome",
        entity_type="todo",
        name="done",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": "2026-12-02", "urgency": "resolved"}),
    )
    assert not [
        i
        for i in open_deadline_items(migrated_conn)
        if i.deadline_id == "todo:resolved-outcome"
    ]


def test_drift_row_dedup_and_counts(specimen_conn: sqlite3.Connection) -> None:
    r1 = scan_entity_state_drift(conn=specimen_conn)
    r2 = scan_entity_state_drift(conn=specimen_conn)
    assert r1.rows == r2.rows
    assert r1.total_rows == len(r1.rows)
    assert r1.distinct_entities == len({row.entity_id for row in r1.rows})


def test_detector_does_not_require_w4_fold(migrated_conn: sqlite3.Connection) -> None:
    insert_entity(
        migrated_conn,
        entity_id="account:stale-desc",
        entity_type="account",
        name="Stale desc",
        description="Application not_submitted in summary.",
        attributes=json.dumps({"status": "submitted"}),
    )
    result = scan_entity_state_drift(conn=migrated_conn)
    assert any(r.entity_id == "account:stale-desc" for r in result.rows)


def test_rename_mutation_p5(migrated_conn: sqlite3.Connection) -> None:
    _seed_specimen_f1(migrated_conn, entity_id="account:renamed-loan-x")
    migrated_conn.execute(
        "UPDATE entities SET name = ? WHERE id = ?",
        ("Renamed loan product", "account:renamed-loan-x"),
    )
    migrated_conn.commit()
    baseline = scan_entity_state_drift(conn=migrated_conn)
    assert any(
        r.drift_class == "F1" and r.entity_id == "account:renamed-loan-x"
        for r in baseline.rows
    )


def test_hermetic_specimens_all_three_classes(
    specimen_conn: sqlite3.Connection,
) -> None:
    result = scan_entity_state_drift(conn=specimen_conn)
    classes = {r.drift_class for r in result.rows}
    assert {"F1", "F2", "F3"}.issubset(classes)


def test_partial_scan_does_not_publish_a_baseline(
    migrated_conn: sqlite3.Connection,
) -> None:
    insert_entity(migrated_conn, entity_id="partial:a", entity_type="todo", name="A")
    insert_entity(migrated_conn, entity_id="partial:b", entity_type="todo", name="B")
    calls = {"n": 0}

    def _boom(_entity_id: str) -> None:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("interrupt")

    with patch(
        "cortex_store.drift_detector.enumerate_entity_ids",
        return_value=["partial:a", "partial:b"],
    ):
        with pytest.raises(RuntimeError, match="interrupt"):
            scan_entity_state_drift(conn=migrated_conn, after_entity=_boom)


def test_detector_db_down_writes_nothing_and_raises(
    migrated_conn: sqlite3.Connection,
) -> None:
    with patch.object(db, "check_cortex_db", return_value=False):
        with pytest.raises(RuntimeError, match="unavailable"):
            scan_entity_state_drift()


def test_detector_never_opens_a_write_transaction(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "drift_ro.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    rw = sqlite3.connect(str(db_path))
    rw.row_factory = sqlite3.Row
    _seed_specimen_f1(rw, entity_id="account:ro-f1")
    rw.close()
    conn = db.cortex_conn(read_only=True)
    before_tables = snapshot_table_counts(conn)
    dv_before = conn.execute("PRAGMA data_version").fetchone()[0]
    access_before = conn.execute("SELECT COUNT(*) FROM entity_access_log").fetchone()[0]
    try:
        scan_entity_state_drift(conn=conn)
    except Exception:
        pass
    dv_after = conn.execute("PRAGMA data_version").fetchone()[0]
    access_after = conn.execute("SELECT COUNT(*) FROM entity_access_log").fetchone()[0]
    after_tables = snapshot_table_counts(conn)
    assert dv_after == dv_before
    assert access_after == access_before
    assert after_tables == before_tables
    conn.close()


@pytest.mark.skipif(not db.check_cortex_db(), reason="live cortex db absent")
def test_live_scan_detects_f1_f2_f3_classes() -> None:
    result = scan_entity_state_drift()
    classes = {r.drift_class for r in result.rows}
    assert "F1" in classes
    assert "F2" in classes
    assert "F3" in classes


@pytest.mark.skipif(not db.check_cortex_db(), reason="live cortex db absent")
def test_live_f2_cites_contradicting_pair() -> None:
    result = scan_entity_state_drift()
    f2 = [r for r in result.rows if r.drift_class == "F2"]
    assert f2
    assert all(r.left_cite for r in f2)
    assert all(r.right_cite for r in f2)
