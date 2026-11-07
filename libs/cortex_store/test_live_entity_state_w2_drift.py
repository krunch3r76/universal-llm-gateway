"""W2 live entity-state drift detector tests."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store import db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.drift_detector import (
    enumerate_entity_ids,
    open_deadline_items,
    scan_entity_state_drift,
)
from cortex_store.routes.deadlines import _list_deadlines_impl


def _now() -> str:
    return datetime.now(UTC).isoformat()


def snapshot_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        for name in sorted(tables)
    }


def _db_snapshot(path: Path) -> tuple[int, int, dict[str, int]]:
    obs = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    obs.row_factory = sqlite3.Row
    dv = int(obs.execute("PRAGMA data_version").fetchone()[0])
    access = int(obs.execute("SELECT COUNT(*) FROM entity_access_log").fetchone()[0])
    counts = snapshot_table_counts(obs)
    obs.close()
    return dv, access, counts


def _insert_status_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
    confidence: str = "confirmed",
    review_status: str | None = None,
    review_notes: str | None = None,
    created_at: str | None = None,
    observed_at: str | None = None,
    derivation_type: str | None = None,
    evidence_uris: str | None = None,
    superseded_by: int | None = None,
    assertion_id: int | None = None,
) -> int:
    ts = created_at or _now()
    obs = observed_at or ts
    pf = f"status({entity_id}, operational, current)"
    if assertion_id is not None:
        conn.execute(
            "INSERT INTO assertions (id, entity_id, claim, confidence, predicate_form, "
            "review_status, review_notes, derivation_type, evidence_uris, superseded_by, "
            "entrenchment_score, created_at, updated_at, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, ?)",
            (
                assertion_id,
                entity_id,
                claim,
                confidence,
                pf,
                review_status,
                review_notes,
                derivation_type or "inference",
                evidence_uris,
                superseded_by,
                ts,
                ts,
                obs,
            ),
        )
    else:
        cur = conn.execute(
            "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
            "review_status, review_notes, derivation_type, evidence_uris, superseded_by, "
            "entrenchment_score, created_at, updated_at, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, ?)",
            (
                entity_id,
                claim,
                confidence,
                pf,
                review_status,
                review_notes,
                derivation_type or "inference",
                evidence_uris,
                superseded_by,
                ts,
                ts,
                obs,
            ),
        )
        assertion_id = int(cur.lastrowid or 0)
    conn.commit()
    return assertion_id


def _seed_specimen_f1(conn: sqlite3.Connection, *, entity_id: str) -> None:
    insert_entity(
        conn,
        entity_id=entity_id,
        entity_type="account",
        name="Loan enrollment account",
        description="IDR application not_submitted per servicer letter.",
        attributes=json.dumps(
            {"application_submitted": True, "receipt_id": "receipt-9158"}
        ),
    )
    _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="Older served status",
        confidence="believed",
        created_at="2026-10-01T00:00:00+00:00",
    )
    _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="Newer withheld claim",
        confidence="confirmed",
        review_status="flagged",
        review_notes=None,
        created_at="2026-11-03T00:00:00+00:00",
        derivation_type="direct_observation",
        evidence_uris='["cortex://notes/evidence.md"]',
    )


def _seed_specimen_f2(conn: sqlite3.Connection) -> tuple[str, str]:
    org_id = "organization:acme-pharmacy"
    window_id = "todo:acme-benefits-enrollment-window"
    choice_id = "document:acme-benefits-choice-rollup"
    insert_entity(conn, entity_id=org_id, entity_type="organization", name="Acme Pharmacy")
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
                "annual_2027_confirmation_number": "40409621",
                "annual_2027_state": "enrollment_completed_root_verified",
            }
        ),
    )
    conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-11-01T00:00:00+00:00", choice_id),
    )
    conn.executemany(
        "INSERT INTO relationships (from_entity, to_entity, type, active) VALUES (?, ?, ?, 1)",
        [(window_id, org_id, "pertains_to"), (choice_id, org_id, "references")],
    )
    conn.commit()
    return window_id, choice_id


def _seed_specimen_f3(conn: sqlite3.Connection, *, entity_id: str) -> None:
    insert_entity(
        conn,
        entity_id=entity_id,
        entity_type="plan",
        name="Concern substrate plan",
        description="Staging plan row.",
    )
    _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="Served staging status",
        confidence="believed",
        created_at="2026-10-20T00:00:00+00:00",
    )
    _insert_status_assertion(
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


@pytest.mark.parametrize(
    "desc,expect_f1",
    [
        ("Payment not submitted on file.", True),
        ("Payment not submitted pending.", True),
        ("Account remains unsubmitted.", True),
        ("xnot_submittedy token only.", False),
        ("OPEN ENROLLMENT window open.", False),
    ],
)
def test_f1_a7_negation_boundaries(
    migrated_conn: sqlite3.Connection, desc: str, expect_f1: bool
) -> None:
    eid = f"account:f1-{hash(desc) % 10_000}"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="account",
        name="Boundary",
        description=desc,
        attributes=json.dumps({"enrollment_confirmed": True}),
    )
    hits = [
        r
        for r in scan_entity_state_drift(conn=migrated_conn).rows
        if r.entity_id == eid and r.drift_class == "F1"
    ]
    assert bool(hits) is expect_f1


def test_f1_enrollment_confirmed_string_true(migrated_conn: sqlite3.Connection) -> None:
    insert_entity(
        migrated_conn,
        entity_id="account:f1-str-true",
        entity_type="account",
        name="S",
        description="Status not_submitted.",
        attributes=json.dumps({"enrollment_confirmed": "true"}),
    )
    assert any(
        r.entity_id == "account:f1-str-true" for r in scan_entity_state_drift(conn=migrated_conn).rows
    )


def test_f2_walgreens_style_pair(specimen_conn: sqlite3.Connection) -> None:
    result = scan_entity_state_drift(conn=specimen_conn)
    f2 = [r for r in result.rows if r.drift_class == "F2"]
    assert len(f2) == 1
    assert f2[0].entity_id == f2[0].left_cite
    assert "acme-benefits-choice" in f2[0].right_cite


def test_f2_older_evidence_zero(migrated_conn: sqlite3.Connection) -> None:
    org = "organization:hub-org"
    window = "todo:hub-window"
    choice = "document:hub-choice-old"
    insert_entity(migrated_conn, entity_id=org, entity_type="organization", name="Hub Org")
    insert_entity(
        migrated_conn,
        entity_id=window,
        entity_type="todo",
        name="hub benefits enrollment window",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": "2026-12-02"}),
    )
    migrated_conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-10-01T00:00:00+00:00", window),
    )
    insert_entity(
        migrated_conn,
        entity_id=choice,
        entity_type="document",
        name="hub benefits choice",
        attributes=json.dumps({"enrollment_confirmed": True}),
    )
    migrated_conn.execute(
        "UPDATE entities SET created_at = ?, updated_at = ? WHERE id = ?",
        ("2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00", choice),
    )
    migrated_conn.executemany(
        "INSERT INTO relationships (from_entity, to_entity, type, active) VALUES (?, ?, ?, 1)",
        [(window, org, "pertains_to"), (choice, org, "references")],
    )
    migrated_conn.commit()
    assert not [r for r in scan_entity_state_drift(conn=migrated_conn).rows if r.drift_class == "F2"]


def test_f2_token_only_sibling_zero(migrated_conn: sqlite3.Connection) -> None:
    org = "organization:shared-hub"
    window = "todo:benefits-window-x"
    sibling = "document:benefits-window-x-shadow"
    insert_entity(migrated_conn, entity_id=org, entity_type="organization", name="shared hub")
    insert_entity(
        migrated_conn,
        entity_id=window,
        entity_type="todo",
        name="benefits window x",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": "2026-12-02"}),
    )
    migrated_conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-09-01T00:00:00+00:00", window),
    )
    insert_entity(
        migrated_conn,
        entity_id=sibling,
        entity_type="document",
        name="benefits window x shadow",
    )
    migrated_conn.executemany(
        "INSERT INTO relationships (from_entity, to_entity, type, active) VALUES (?, ?, ?, 1)",
        [(window, org, "pertains_to"), (sibling, org, "references")],
    )
    migrated_conn.commit()
    assert not [r for r in scan_entity_state_drift(conn=migrated_conn).rows if r.drift_class == "F2"]


def test_f2_attribute_only_unrelated_zero(migrated_conn: sqlite3.Connection) -> None:
    org = "organization:retail-hub"
    window = "todo:retail-enrollment-window"
    unrelated = "document:marine-finance-choice"
    insert_entity(migrated_conn, entity_id=org, entity_type="organization", name="retail hub")
    insert_entity(
        migrated_conn,
        entity_id=window,
        entity_type="todo",
        name="retail enrollment window",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": "2026-12-02"}),
    )
    migrated_conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-09-01T00:00:00+00:00", window),
    )
    insert_entity(
        migrated_conn,
        entity_id=unrelated,
        entity_type="document",
        name="marine finance choice",
        attributes=json.dumps({"enrollment_confirmed": True}),
    )
    migrated_conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-11-05T00:00:00+00:00", unrelated),
    )
    migrated_conn.executemany(
        "INSERT INTO relationships (from_entity, to_entity, type, active) VALUES (?, ?, ?, 1)",
        [(window, org, "pertains_to"), (unrelated, org, "references")],
    )
    migrated_conn.commit()
    assert not [r for r in scan_entity_state_drift(conn=migrated_conn).rows if r.drift_class == "F2"]


def test_f2_inactive_edge_ignored(migrated_conn: sqlite3.Connection) -> None:
    org = "organization:inactive-hub"
    window = "todo:inactive-window"
    choice = "document:inactive-choice"
    insert_entity(migrated_conn, entity_id=org, entity_type="organization", name="inactive hub")
    insert_entity(
        migrated_conn,
        entity_id=window,
        entity_type="todo",
        name="inactive enrollment window",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": "2026-12-02"}),
    )
    migrated_conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-09-01T00:00:00+00:00", window),
    )
    insert_entity(
        migrated_conn,
        entity_id=choice,
        entity_type="document",
        name="inactive enrollment choice",
        attributes=json.dumps({"enrollment_confirmed": True}),
    )
    migrated_conn.execute(
        "UPDATE entities SET created_at = ? WHERE id = ?",
        ("2026-11-05T00:00:00+00:00", choice),
    )
    migrated_conn.executemany(
        "INSERT INTO relationships (from_entity, to_entity, type, active) VALUES (?, ?, ?, ?)",
        [(window, org, "pertains_to", 1), (choice, org, "references", 0)],
    )
    migrated_conn.commit()
    assert not [r for r in scan_entity_state_drift(conn=migrated_conn).rows if r.drift_class == "F2"]


def test_f3_withheld_null_and_nonnull_reason(migrated_conn: sqlite3.Connection) -> None:
    eid_null = "plan:f3-null"
    eid_nn = "plan:f3-nn"
    _seed_specimen_f3(migrated_conn, entity_id=eid_nn)
    insert_entity(migrated_conn, entity_id=eid_null, entity_type="plan", name="null plan")
    _insert_status_assertion(
        migrated_conn,
        entity_id=eid_null,
        claim="served",
        confidence="believed",
        created_at="2026-10-01T00:00:00+00:00",
    )
    _insert_status_assertion(
        migrated_conn,
        entity_id=eid_null,
        claim="withheld",
        confidence="confirmed",
        review_status="flagged",
        review_notes=None,
        created_at="2026-11-01T00:00:00+00:00",
        derivation_type="direct_observation",
        evidence_uris='["cortex://x"]',
    )
    rows = scan_entity_state_drift(conn=migrated_conn).rows
    assert any(r.entity_id == eid_null and r.reason is None for r in rows if r.drift_class == "F3")
    assert any(r.entity_id == eid_nn and r.reason for r in rows if r.drift_class == "F3")


def test_f3_unrelated_newer_claim_zero(migrated_conn: sqlite3.Connection) -> None:
    eid = "plan:f3-unrelated"
    insert_entity(migrated_conn, entity_id=eid, entity_type="plan", name="u")
    _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="served",
        confidence="confirmed",
        created_at="2026-10-01T00:00:00+00:00",
        evidence_uris='["cortex://a"]',
    )
    migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, derivation_type, "
        "entrenchment_score, created_at, updated_at) VALUES (?, ?, 'confirmed', "
        "'inference', 0.5, ?, ?)",
        (eid, "describes(plan:f3-unrelated, unrelated)", "2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00+00:00"),
    )
    migrated_conn.commit()
    assert not [
        r
        for r in scan_entity_state_drift(conn=migrated_conn).rows
        if r.entity_id == eid and r.surface == "current_status.served"
    ]


def test_f3_superseded_newer_zero(migrated_conn: sqlite3.Connection) -> None:
    eid = "plan:f3-super"
    insert_entity(migrated_conn, entity_id=eid, entity_type="plan", name="s")
    old = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="served",
        confidence="believed",
        created_at="2026-10-01T00:00:00+00:00",
    )
    newer = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="new status",
        confidence="confirmed",
        created_at="2026-11-01T00:00:00+00:00",
        evidence_uris='["cortex://b"]',
    )
    migrated_conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?", (newer, old)
    )
    migrated_conn.commit()
    assert not [
        r
        for r in scan_entity_state_drift(conn=migrated_conn).rows
        if r.entity_id == eid and r.surface == "current_status.served"
    ]


def test_a6_equal_timestamp_id_ordering(migrated_conn: sqlite3.Connection) -> None:
    eid = "plan:a6-order"
    insert_entity(migrated_conn, entity_id=eid, entity_type="plan", name="order")
    ts = "2026-11-01T12:00:00+00:00"
    served = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="served",
        confidence="believed",
        created_at=ts,
    )
    newer = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="newer",
        confidence="confirmed",
        created_at=ts,
        evidence_uris='["cortex://c"]',
        derivation_type="direct_observation",
    )
    migrated_conn.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (_now(), eid))
    migrated_conn.commit()
    hit = [
        r
        for r in scan_entity_state_drift(conn=migrated_conn).rows
        if r.entity_id == eid and r.surface == "current_status.served"
    ]
    assert hit and str(newer) in hit[0].right_cite and str(served) not in hit[0].right_cite


def test_a5_withheld_more_than_three(migrated_conn: sqlite3.Connection) -> None:
    eid = "plan:a5-cap"
    insert_entity(migrated_conn, entity_id=eid, entity_type="plan", name="cap")
    _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="served",
        confidence="believed",
        created_at="2026-09-01T00:00:00+00:00",
    )
    for i in range(4):
        _insert_status_assertion(
            migrated_conn,
            entity_id=eid,
            claim=f"flag {i}",
            confidence="confirmed",
            review_status="flagged",
            created_at=f"2026-11-0{i + 1}T00:00:00+00:00",
            derivation_type="direct_observation",
            evidence_uris='["cortex://d"]',
        )
    assert any(
        r.entity_id == eid and r.drift_class == "F3" for r in scan_entity_state_drift(conn=migrated_conn).rows
    )


def test_population_null_lifecycle_included(migrated_conn: sqlite3.Connection) -> None:
    insert_entity(migrated_conn, entity_id="entity:null-life", entity_type="todo", name="N")
    migrated_conn.execute("UPDATE entities SET lifecycle = NULL WHERE id = ?", ("entity:null-life",))
    migrated_conn.commit()
    assert "entity:null-life" in enumerate_entity_ids(migrated_conn)


def test_open_deadlines_match_list_deadlines_impl(
    migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch, migrated_conn: sqlite3.Connection
) -> None:
    _seed_specimen_f2(migrated_conn)
    bind_cortex_db(monkeypatch, migrated_db_path)
    listed = _list_deadlines_impl(include_resolved=False)["items"]
    open_items = open_deadline_items(migrated_conn)
    assert {i.deadline_id for i in open_items} == {str(r.get("deadline_id")) for r in listed}


def test_resolved_deadline_exclusions(migrated_conn: sqlite3.Connection) -> None:
    insert_entity(
        migrated_conn,
        entity_id="todo:resolved-urgency",
        entity_type="todo",
        name="done",
        workflow_state="open",
        attributes=json.dumps({"deadline_date": "2026-12-02", "urgency": "resolved"}),
    )
    assert "todo:resolved-urgency" not in {i.deadline_id for i in open_deadline_items(migrated_conn)}


def test_drift_row_dedup_and_counts(specimen_conn: sqlite3.Connection) -> None:
    r1 = scan_entity_state_drift(conn=specimen_conn)
    r2 = scan_entity_state_drift(conn=specimen_conn)
    assert r1.rows == r2.rows
    assert r1.total_rows == len(r1.rows)
    assert r1.distinct_entities == len({row.entity_id for row in r1.rows})


def test_rename_mutation_p5(migrated_conn: sqlite3.Connection) -> None:
    _seed_specimen_f1(migrated_conn, entity_id="account:orig-loan")
    orig = scan_entity_state_drift(conn=migrated_conn)
    _seed_specimen_f1(migrated_conn, entity_id="account:renamed-loan-x")
    ren = scan_entity_state_drift(conn=migrated_conn)
    o = {(r.drift_class, r.surface) for r in orig.rows if r.entity_id == "account:orig-loan"}
    n = {(r.drift_class, r.surface) for r in ren.rows if r.entity_id == "account:renamed-loan-x"}
    assert o == n and o


def test_hermetic_specimens_all_three_classes(specimen_conn: sqlite3.Connection) -> None:
    assert {"F1", "F2", "F3"}.issubset(
        {r.drift_class for r in scan_entity_state_drift(conn=specimen_conn).rows}
    )


def test_partial_scan_does_not_publish_a_baseline(
    migrated_db_template: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    insert_path = tmp_path / "partial.db"
    copy_template_db(migrated_db_template, insert_path)
    rw = sqlite3.connect(insert_path)
    rw.row_factory = sqlite3.Row
    insert_entity(rw, entity_id="partial:a", entity_type="todo", name="A")
    insert_entity(rw, entity_id="partial:b", entity_type="todo", name="B")
    rw.close()
    bind_cortex_db(monkeypatch, insert_path)
    before = _db_snapshot(insert_path)
    calls = {"n": 0}

    def _boom(_: str) -> None:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("interrupt")

    with patch(
        "cortex_store.drift_detector.enumerate_entity_ids", return_value=["partial:a", "partial:b"]
    ):
        with pytest.raises(RuntimeError, match="interrupt"):
            scan_entity_state_drift(after_entity=_boom)
    assert _db_snapshot(insert_path) == before


def test_detector_db_down_writes_nothing_and_raises(
    migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    before = _db_snapshot(migrated_db_path)
    with patch.object(db, "check_cortex_db", return_value=False):
        with pytest.raises(RuntimeError, match="unavailable"):
            scan_entity_state_drift()
    assert _db_snapshot(migrated_db_path) == before


def test_detector_never_opens_a_write_transaction(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = tmp_path / "drift_ro.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    rw = sqlite3.connect(str(db_path))
    rw.row_factory = sqlite3.Row
    _seed_specimen_f1(rw, entity_id="account:ro-f1")
    rw.close()
    before = _db_snapshot(db_path)
    caplog.set_level(logging.WARNING)
    with caplog.at_level(logging.ERROR):
        scan_entity_state_drift()
    assert _db_snapshot(db_path) == before
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


_LIVE_DB = db.check_cortex_db()


@pytest.mark.skipif(not _LIVE_DB, reason="live cortex db absent on this seat")
def test_live_specimen_f1_account() -> None:
    rows = [r for r in scan_entity_state_drift().rows if r.entity_id == "account:edfinancial-student-loan-9158"]
    assert any(r.drift_class == "F1" for r in rows)


@pytest.mark.skipif(not _LIVE_DB, reason="live cortex db absent on this seat")
def test_live_specimen_f2_walgreens() -> None:
    rows = [
        r
        for r in scan_entity_state_drift().rows
        if r.entity_id == "todo:walgreens-benefits-enrollment-window" and r.drift_class == "F2"
    ]
    assert rows
    assert "document:walgreens-benefits-choice-20261030" in rows[0].right_cite


@pytest.mark.skipif(not _LIVE_DB, reason="live cortex db absent on this seat")
def test_live_specimen_f3_concern_substrate() -> None:
    rows = [
        r
        for r in scan_entity_state_drift().rows
        if r.entity_id == "plan:cortex-concern-substrate" and r.drift_class == "F3"
    ]
    assert rows
