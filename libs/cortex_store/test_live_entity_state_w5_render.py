"""W5 live entity-state render-on-read tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.card import get_entity_card
from cortex_store.dispatch_ops._entity_get_preview import build_entity_preview
from cortex_store.entity_state_render import derive_status_view


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    assertion_id: int | None = None,
    predicate_form: str | None = None,
    valid_until: str | None = None,
) -> int:
    ts = created_at or _now()
    obs = observed_at or ts
    pf = predicate_form or f"status({entity_id}, submitted, current)"
    cols = (
        "entity_id, claim, confidence, predicate_form, review_status, review_notes, "
        "derivation_type, evidence_uris, valid_from, valid_until, entrenchment_score, "
        "created_at, updated_at, observed_at"
    )
    if assertion_id is not None:
        conn.execute(
            f"INSERT INTO assertions (id, {cols}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, ?)",
            (
                assertion_id,
                entity_id,
                claim,
                confidence,
                pf,
                review_status,
                review_notes,
                derivation_type or "direct_observation",
                evidence_uris or '["cortex://notes/evidence.md"]',
                None,
                valid_until,
                ts,
                ts,
                obs,
            ),
        )
    else:
        cur = conn.execute(
            f"INSERT INTO assertions ({cols}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, ?)",
            (
                entity_id,
                claim,
                confidence,
                pf,
                review_status,
                review_notes,
                derivation_type or "direct_observation",
                evidence_uris or '["cortex://notes/evidence.md"]',
                None,
                valid_until,
                ts,
                ts,
                obs,
            ),
        )
        assertion_id = int(cur.lastrowid or 0)
    conn.commit()
    return assertion_id


def _seed_specimen_one(conn: sqlite3.Connection, entity_id: str = "account:specimen-w5-one") -> int:
    insert_entity(
        conn,
        entity_id=entity_id,
        entity_type="account",
        name="Loan account",
        description="IDR application not_submitted per servicer letter.",
        attributes=json.dumps({"application_submitted": True}),
    )
    _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="Older served",
        confidence="believed",
        created_at="2026-10-01T00:00:00+00:00",
        predicate_form=f"status({entity_id}, not_submitted, current)",
    )
    newer = _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="Newer flagged",
        review_status="flagged",
        review_notes=None,
        created_at="2026-11-03T00:00:00+00:00",
        predicate_form=f"status({entity_id}, submitted, current)",
    )
    return newer


def test_import_card_without_cycle() -> None:
    out = subprocess.run(
        [sys.executable, "-c", "import cortex_store.card"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr


@pytest.mark.offline
def test_specimen_one_template_sentence(migrated_conn: sqlite3.Connection) -> None:
    newer = _seed_specimen_one(migrated_conn)
    card = get_entity_card(migrated_conn, entity_id="account:specimen-w5-one", source="boot")
    assert "application_submitted=true" in (card.get("summary_row") or "")
    assert f"a:{newer}" in (card.get("summary_row") or "")
    assert card.get("authored_note", {}).get("text", "").startswith("IDR application")
    assert "not_submitted" not in (card.get("summary_row") or "")


@pytest.mark.offline
def test_f1_fixture_authored_note_and_description_bytes(migrated_conn: sqlite3.Connection) -> None:
    eid = "account:specimen-w5-f1"
    desc = "Payment not submitted on file."
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="account",
        name="F1",
        description=desc,
        attributes=json.dumps({"application_submitted": True}),
    )
    before = migrated_conn.execute(
        "SELECT description FROM entities WHERE id = ?", (eid,)
    ).fetchone()["description"]
    _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="newer",
        review_status="flagged",
        review_notes=None,
        created_at="2026-11-04T00:00:00+00:00",
    )
    card = get_entity_card(migrated_conn, entity_id=eid, source="boot")
    assert "newer claim" in (card.get("summary_row") or "")
    assert card["authored_note"]["text"] == desc
    after = migrated_conn.execute(
        "SELECT description FROM entities WHERE id = ?", (eid,)
    ).fetchone()["description"]
    assert after == before == desc


@pytest.mark.offline
def test_f3_withheld_visible_with_reason(migrated_conn: sqlite3.Connection) -> None:
    eid = "plan:specimen-w5-f3"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="plan",
        name="F3",
        description="Staging",
        attributes=json.dumps({"application_submitted": True}),
    )
    _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="served",
        confidence="believed",
        created_at="2026-10-01T00:00:00+00:00",
        predicate_form=f"status({eid}, not_submitted, current)",
    )
    wid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="withheld",
        review_status="flagged",
        review_notes="predicate normalize: invention: detail",
        created_at="2026-11-05T00:00:00+00:00",
    )
    cols = {r[1] for r in migrated_conn.execute("PRAGMA table_info(assertions)")}
    if "reviewer" in cols:
        migrated_conn.execute(
            "UPDATE assertions SET reviewer = ? WHERE id = ?", ("operator", wid)
        )
        migrated_conn.commit()
    card = get_entity_card(migrated_conn, entity_id=eid, source="boot")
    suffix = card.get("summary_row") or ""
    withheld = card.get("current_status", {}).get("withheld_newer") or []
    assert any(w.get("reason") == "invention" for w in withheld)
    assert "pin withholds" in suffix


@pytest.mark.offline
def test_f4_snapshot_claim_not_chosen(migrated_conn: sqlite3.Connection) -> None:
    eid = "account:specimen-w5-f4"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="account",
        name="F4",
        attributes=json.dumps({"application_submitted": False}),
    )
    snap = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="snapshot",
        created_at="2026-12-01T00:00:00+00:00",
        valid_until="2020-01-01T00:00:00+00:00",
    )
    card = get_entity_card(migrated_conn, entity_id=eid, source="boot")
    assert f"a:{snap}" not in (card.get("summary_row") or "")


@pytest.mark.offline
def test_legacy_todo_workflow_state_without_provenance_keeps_description(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = "todo:legacy-open"
    desc = "Original mission text."
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="Legacy",
        description=desc,
        workflow_state="open",
    )
    preview_before = build_entity_preview(migrated_conn, entity_id=eid)
    card = get_entity_card(migrated_conn, entity_id=eid, source="boot")
    assert card.get("summary_row") == desc
    assert card.get("authored_note") is None
    assert preview_before["short_summary"] == desc


@pytest.mark.offline
def test_missing_provenance_renders_unverified_with_claim_cite(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = "account:no-prov"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="account",
        name="No prov",
        attributes=json.dumps({"application_submitted": True}),
    )
    aid = _insert_status_assertion(migrated_conn, entity_id=eid, claim="c")
    card = get_entity_card(migrated_conn, entity_id=eid, source="boot")
    row = card.get("summary_row") or ""
    assert "application_submitted=true" in row
    assert "[unverified]" in row
    assert f"a:{aid}" in row


@pytest.mark.offline
def test_render_does_not_update_entities_row(migrated_conn: sqlite3.Connection) -> None:
    eid = "account:read-only-desc"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="account",
        name="RO",
        description="stay",
        attributes=json.dumps({"application_submitted": True}),
    )
    before = dict(
        migrated_conn.execute(
            "SELECT description, updated_at FROM entities WHERE id = ?", (eid,)
        ).fetchone()
    )
    get_entity_card(migrated_conn, entity_id=eid, source="boot")
    after = dict(
        migrated_conn.execute(
            "SELECT description, updated_at FROM entities WHERE id = ?", (eid,)
        ).fetchone()
    )
    assert before == after


@pytest.mark.offline
def test_concern_head_branch_unchanged(migrated_conn: sqlite3.Connection) -> None:
    eid = "concern:specimen-w5"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="concern",
        name="Concern",
        description="Concern prose",
    )
    migrated_conn.execute(
        "INSERT INTO concern_head (concern_id, record_ref, revision) VALUES (?, ?, 1)",
        (eid, "cortex://notes/concern.md"),
    )
    migrated_conn.commit()
    card = get_entity_card(migrated_conn, entity_id=eid, source="boot")
    assert card.get("summary_row") == "Concern prose"
    assert card.get("authored_note") is None


@pytest.mark.offline
def test_workflow_state_reversed_to_null_renders_absent(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = "todo:wf-reverse"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="WF",
        workflow_state=None,
    )
    row = dict(
        migrated_conn.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
    )
    view = derive_status_view(migrated_conn, eid, row)
    assert view is None
