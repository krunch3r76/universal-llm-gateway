"""Wave 1 graduation honesty — AC1–AC10 fixtures (todo:cortex-predicate-normalize-graduation-honesty)."""

from __future__ import annotations

import sqlite3

import pytest
from predicate_form.classes import class_6_check
from predicate_form.entity_resolve import StaticEntityResolver
from predicate_form.invention_resubjection_guards import check_invention
from predicate_form.parser import parse

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.card import get_entity_card
from cortex_store.dispatch_ops._assertions_shared import (
    _emit_predicate_form_normalize_events,
)
from cortex_store.renormalize import dry_run_stratify, t0_adjudicate_flagged
from cortex_store.routes.assertions import (
    _create_assertion_impl,
    _update_assertion_impl,
)
from cortex_store.routes.assertions._shared import _build_predicate_form_normalize
from cortex_store.test_assertion_predicate_form_normalize import (
    _insert_assertion,
    _patch_update,
)


class _NoCloseConn:
    """Wrapper so route code that closes cortex_conn does not tear down the fixture."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):  # noqa: ANN001
        return getattr(self._conn, name)

    def close(self) -> None:
        return None

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, *args: object) -> None:
        return None


def _seed_session_edge_types(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO session_edge_types (type, description, directional) "
        "VALUES ('supersedes', 'supersession edge', 1)"
    )
    conn.commit()


def _patch_create_and_supersede(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    wrapper = _NoCloseConn(conn)
    for mod in ("_create", "_supersede", "_update"):
        prefix = f"cortex_store.routes.assertions.{mod}"
        monkeypatch.setattr(f"{prefix}.cortex_conn", lambda w=wrapper: w)
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.guard_assertion_write",
        lambda *a, **kw: type(
            "G",
            (),
            {
                "allowed": True,
                "block_detail": "",
                "review_status": None,
                "contradiction_warnings": [],
            },
        )(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.check_contradictions",
        lambda *a, **kw: type("C", (), {"flagged": False})(),
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
        "cortex_store.routes.assertions._supersede.analyze_assertion_impact",
        lambda *a, **kw: type(
            "I", (), {"likely_supersedes": [], "touched_assertions": []}
        )(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.enrich_old_assertion_events",
        lambda *a, **kw: None,
    )
    _seed_session_edge_types(conn)


@pytest.mark.parametrize(
    ("predicate_form", "entity_id", "expected"),
    [
        ("status(service:x, failure, current)", "service:x", False),
        ("status(service:x, pending)", "service:x", True),
        ("status(service:x, pending, former)", "service:x", True),
        ("status(service:x, current)", "service:x", True),
        ("workflow_status(service:x, failure, current)", "service:x", False),
        ("has_attribute(person:x, workflow_state, pending)", "person:x", True),
    ],
)
def test_ac1_class_6_fixtures(predicate_form: str, entity_id: str, expected: bool) -> None:
    p = parse(predicate_form)
    assert class_6_check(entity_id, p) is expected


@pytest.mark.parametrize(
    ("claim", "predicate_form", "expected"),
    [
        ("VERIFIED COMPLETE for the todo.", "status(todo:x, verified_complete)", False),
        ("FOLLOW-UP DONE on the item.", "status(todo:x, follow_up_done)", False),
        ("VENDOR CHECKS total 515.87.", "has(asset:x, vendor_checks, 515.87)", False),
        ("follow-up done", "status(todo:x, follow-up-done)", False),
        ("No matching token.", "status(todo:x, operational, current)", True),
    ],
)
def test_ac2_space_join_invention(claim: str, predicate_form: str, expected: bool) -> None:
    p = parse(predicate_form)
    assert check_invention(claim, p) is expected


def test_ac3a_chase_mortgage_served_withheld_and_t0_clear(
    migrated_conn: sqlite3.Connection,
) -> None:
    """AC3a mechanical — account:chase-mortgage-8787 pin + guard re-fire + T0."""
    entity_id = "account:chase-mortgage-8787"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="account", name="Chase")
    older_pf = f"status({entity_id}, operational, current)"
    newer_pf = f"status({entity_id}, quiet, current)"
    migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "review_status, created_at, updated_at) VALUES (?, ?, 'believed', ?, "
        "'committed', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (entity_id, "Older operational status (a:8402 analog).", older_pf),
    )
    cur = migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "review_status, review_notes, normalizer_version, created_at, updated_at) "
        "VALUES (?, ?, 'believed', ?, 'flagged', "
        "'predicate normalize: class6_generic_state: requires_human_review', "
        "'v1.3.1', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')",
        (entity_id, "Brief mood quiet status (a:29834 analog).", newer_pf),
    )
    migrated_conn.commit()
    newer_id = int(cur.lastrowid or 0)

    card = get_entity_card(migrated_conn, entity_id=entity_id)
    cs = card["current_status"]
    assert cs["served"] is not None
    assert cs["withheld_newer"]
    withheld_ids = {entry["assertion_id"] for entry in cs["withheld_newer"]}
    assert newer_id in withheld_ids
    assert any(
        entry.get("reason") == "class6_generic_state" for entry in cs["withheld_newer"]
    )

    p = parse(newer_pf)
    assert class_6_check(entity_id, p) is False

    result = t0_adjudicate_flagged(migrated_conn, dry_run=False)
    assert newer_id in result["sample_cleared_ids"]
    row = migrated_conn.execute(
        "SELECT review_status, reviewer FROM assertions WHERE id = ?", (newer_id,)
    ).fetchone()
    assert row[0] == "committed"
    assert str(row[1]).startswith("normalizer:")


def test_ac4_stale_serve_shows_withheld_not_silent_promote(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "service:stale-serve-fixture"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="service", name="S")
    older_pf = f"status({entity_id}, operational, current)"
    newer_pf = f"status({entity_id}, failure, current)"
    migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "review_status, created_at, updated_at) VALUES (?, ?, 'believed', ?, "
        "'committed', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (entity_id, "Older operational status.", older_pf),
    )
    cur = migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "review_status, review_notes, created_at, updated_at) VALUES (?, ?, 'believed', ?, "
        "'flagged', 'predicate normalize: class6_generic_state: requires_human_review', "
        "'2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')",
        (entity_id, "Newer flagged failure status.", newer_pf),
    )
    migrated_conn.commit()
    flagged_id = int(cur.lastrowid or 0)

    card = get_entity_card(migrated_conn, entity_id=entity_id)
    cs = card["current_status"]
    assert cs["served"] is not None
    assert cs["withheld_newer"]
    assert any(entry["assertion_id"] == flagged_id for entry in cs["withheld_newer"])


def test_ac5_three_producers_distinct_review_notes(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.routes.assertions._supersede import _supersede_assertion_impl

    _patch_create_and_supersede(monkeypatch, migrated_conn)
    entity_id = "service:review-notes-fixture"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="service", name="R")
    pf_class6 = f"status({entity_id}, pending, current)"

    create_result = _create_assertion_impl(
        {
            "entity_id": entity_id,
            "claim": "Service pending status observed.",
            "confidence": "believed",
            "evidence": "test",
            "predicate_form": pf_class6,
            "derivation_type": "direct_observation",
            "observed_at": "2026-01-01T00:00:00Z",
            "reasoning_summary": "Graduation honesty AC5 fixture.",
        }
    )
    create_id = int(create_result["item"]["id"])
    create_notes = migrated_conn.execute(
        "SELECT review_notes FROM assertions WHERE id = ?", (create_id,)
    ).fetchone()[0]
    assert "class6_generic_state" in str(create_notes)

    update_id = _insert_assertion(migrated_conn, entity_id=entity_id)
    _update_assertion_impl(
        update_id,
        {
            "predicate_form": "status(todo:x, invented_token, current)",
            "claim": "No invented token in claim.",
        },
    )
    update_notes = migrated_conn.execute(
        "SELECT review_notes FROM assertions WHERE id = ?", (update_id,)
    ).fetchone()[0]
    assert "invention" in str(update_notes)
    assert create_notes != update_notes

    old_id = _insert_assertion(migrated_conn, entity_id=entity_id)
    supersede_result = _supersede_assertion_impl(
        {
            "old_assertion_id": old_id,
            "entity_id": entity_id,
            "claim": "Supersede AC5 producer parity fixture.",
            "confidence": "believed",
            "evidence": "test",
            "predicate_form": pf_class6,
            "derivation_type": "direct_observation",
            "observed_at": "2026-01-01T00:00:00Z",
        }
    )
    supersede_id = int(supersede_result["new"]["id"])
    supersede_notes = migrated_conn.execute(
        "SELECT review_notes FROM assertions WHERE id = ?", (supersede_id,)
    ).fetchone()[0]
    supersede_note_str = str(supersede_notes)
    assert "predicate normalize:" in supersede_note_str
    assert any(
        token in supersede_note_str
        for token in ("class6_generic_state", "invention", "resubjection")
    )


def _assert_ac6_envelope(envelope: dict | None) -> None:
    assert envelope is not None, "predicate_form_normalize envelope missing"
    assert envelope.get("requires_human_review"), (
        "fixture predicate must trigger human review (invention/class6/resubjection)"
    )
    assert envelope.get("flag_reasons")
    assert envelope.get("normalization_decision")
    assert envelope.get("suppression_effect")
    assert envelope.get("_next")


def test_ac6_sync_ack_carries_flag_fields_update(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_update(monkeypatch, migrated_conn)
    entity_id = "person:ack-fixture"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="person", name="A")
    aid = _insert_assertion(migrated_conn, entity_id=entity_id)
    result = _update_assertion_impl(
        aid, {"predicate_form": "status(unknown_subject_xyz, ready_to_file)"}
    )
    _assert_ac6_envelope(result.get("predicate_form_normalize"))


def test_ac6_sync_ack_carries_flag_fields_create(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_create_and_supersede(monkeypatch, migrated_conn)
    entity_id = "person:ack-create-fixture"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="person", name="C")
    result = _create_assertion_impl(
        {
            "entity_id": entity_id,
            "claim": "Fixture claim without invented tokens.",
            "confidence": "believed",
            "evidence": "test",
            "predicate_form": "status(unknown_subject_xyz, ready_to_file)",
            "derivation_type": "direct_observation",
            "observed_at": "2026-01-01T00:00:00Z",
        }
    )
    _assert_ac6_envelope(result.get("predicate_form_normalize"))


def test_ac6_sync_ack_carries_flag_fields_supersede(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.routes.assertions._supersede import _supersede_assertion_impl

    _patch_create_and_supersede(monkeypatch, migrated_conn)
    entity_id = "person:ack-supersede-fixture"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="person", name="S")
    old_id = _insert_assertion(migrated_conn, entity_id=entity_id)
    result = _supersede_assertion_impl(
        {
            "old_assertion_id": old_id,
            "entity_id": entity_id,
            "claim": "Supersede AC6 fixture claim.",
            "confidence": "believed",
            "evidence": "test",
            "predicate_form": "status(unknown_subject_xyz, ready_to_file)",
            "derivation_type": "direct_observation",
            "observed_at": "2026-01-01T00:00:00Z",
        }
    )
    _assert_ac6_envelope(result.get("predicate_form_normalize"))


def test_ac7_update_path_flags_and_event_envelope(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict]] = []

    def _fake_record(signal: str, **payload: object) -> None:
        captured.append((signal, dict(payload)))

    _patch_update(monkeypatch, migrated_conn)
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._assertions_shared.record", _fake_record
    )
    entity_id = "person:update-parity"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="person", name="U")
    aid = _insert_assertion(migrated_conn, entity_id=entity_id)
    result = _update_assertion_impl(
        aid, {"predicate_form": "status(unknown_subject_xyz, ready_to_file)"}
    )
    envelope = result.get("predicate_form_normalize")
    assert envelope is not None and envelope.get("requires_human_review"), (
        "update-path fixture must trigger human review"
    )
    _emit_predicate_form_normalize_events(
        assertion_id=aid,
        normalize_payload=envelope,
        session_id="ac7-update-session",
    )
    review_events = [p for s, p in captured if s == "mcp.cortex.predicate.review.required"]
    assert review_events
    payload = review_events[0]
    assert payload.get("session_id") == "ac7-update-session"
    assert payload.get("_next") or payload.get("next_remedy")


def test_ac7_supersede_flags_on_explicit_predicate_form(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.routes.assertions._supersede import _supersede_assertion_impl

    _patch_create_and_supersede(monkeypatch, migrated_conn)
    entity_id = "person:supersede-parity"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="person", name="S")
    old_id = _insert_assertion(migrated_conn, entity_id=entity_id)
    result = _supersede_assertion_impl(
        {
            "old_assertion_id": old_id,
            "entity_id": entity_id,
            "claim": "Phase D fixture claim.",
            "confidence": "believed",
            "evidence": "test",
            "predicate_form": "status(unknown_subject_xyz, ready_to_file)",
            "derivation_type": "direct_observation",
            "observed_at": "2026-01-01T00:00:00Z",
            "session_id": "graduation-test-session",
            "agent": "test",
        }
    )
    new_id = int(result["new"]["id"])
    notes = migrated_conn.execute(
        "SELECT review_status, review_notes FROM assertions WHERE id = ?", (new_id,)
    ).fetchone()
    assert notes[0] == "flagged", "supersede fixture must flag assertion for review"
    assert "predicate normalize:" in str(notes[1])
    assert result.get("predicate_form_normalize") is not None


def test_ac8_dry_run_stratify_and_t0_dry_run(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:t0-fixture"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo", name="T")
    pf = f"status({entity_id}, operational, current)"
    migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "review_status, review_notes, normalizer_version, created_at, updated_at) "
        "VALUES (?, ?, 'believed', ?, 'flagged', "
        "'predicate normalize: legacy_literal: requires_human_review', 'v1.3.1', "
        "datetime('now'), datetime('now'))",
        (entity_id, "Legacy flagged row.", pf),
    )
    migrated_conn.commit()
    stratify = dry_run_stratify(migrated_conn)
    assert stratify
    preview = t0_adjudicate_flagged(migrated_conn, dry_run=True)
    assert preview["dry_run"] is True


def test_ac10_top_k_flagged_carries_epistemic_state(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:topk-flagged"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo", name="T")
    pf = f"describes({entity_id}, flagged_item)"
    migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "review_status, entrenchment_score, created_at, updated_at) "
        "VALUES (?, ?, 'believed', ?, 'flagged', 10.0, datetime('now'), datetime('now'))",
        (entity_id, "Flagged operative claim.", pf),
    )
    migrated_conn.commit()
    pf_unflagged = f"describes({entity_id}, unflagged_item)"
    migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "review_status, entrenchment_score, created_at, updated_at) "
        "VALUES (?, ?, 'believed', ?, 'committed', 5.0, datetime('now'), datetime('now'))",
        (entity_id, "Unflagged operative claim.", pf_unflagged),
    )
    migrated_conn.commit()
    card = get_entity_card(migrated_conn, entity_id=entity_id, top_k=7)
    flagged_rows = [
        a for a in card["top_k_assertions"] if a.get("epistemic_state") == "flagged"
    ]
    assert flagged_rows
    # Amended AC10: unflagged top_k rows carry explicit epistemic_state: null (not omission).
    non_flagged_rows = [
        a for a in card["top_k_assertions"] if a.get("epistemic_state") != "flagged"
    ]
    assert non_flagged_rows
    assert all(a.get("epistemic_state") is None for a in non_flagged_rows)


def test_async_event_carries_reason_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict]] = []

    def _fake_record(signal: str, **payload: object) -> None:
        captured.append((signal, dict(payload)))

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._assertions_shared.record", _fake_record
    )
    from predicate_form import normalize_predicate_domain

    normalize_result = normalize_predicate_domain(
        "person:camelia-mahmoudi",
        "status(unknown_subject_xyz, ready_to_file)",
        claim_text="fixture",
        resolver=StaticEntityResolver({}),
    )
    envelope = _build_predicate_form_normalize(
        "status(unknown_subject_xyz, ready_to_file)", normalize_result
    ).model_dump(mode="json", by_alias=True)
    _emit_predicate_form_normalize_events(
        assertion_id=7,
        normalize_payload=envelope,
        session_id="session-fixture",
    )
    review_events = [p for s, p in captured if s == "mcp.cortex.predicate.review.required"]
    assert review_events, "normalize fixture must emit mcp.cortex.predicate.review.required"
    payload = review_events[0]
    assert payload.get("session_id") == "session-fixture"
    assert payload.get("reason")
