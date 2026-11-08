"""W4 live entity-state fold path tests (spec cortex-live-entity-state-w4-fold-path)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import open_migrated_connection
from cortex_store.card_temporal import install_card_temporal_function
from cortex_store.drift_detector import _evidence_backed, scan_entity_state_drift
from cortex_store.entity_crud import update_entity_impl
from cortex_store.entity_state_fold import (
    FoldResult,
    field_state,
    fold_claim,
    reverse_fold,
    state_sha256,
)
from cortex_store.entity_state_fold_gate import (
    CONTRADICTION_PREFIXES,
    UtteranceInfo,
    is_evidence_backed_w4,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    return str(row[2])


class _ConnDelegate:
    """Thin wrapper so tests can override execute/commit on sqlite3.Connection."""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
        return self._inner.execute(sql, *args)

    def commit(self) -> None:
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()


def _peer_conn(conn: sqlite3.Connection) -> sqlite3.Connection:
    peer = open_migrated_connection(__import__("pathlib").Path(_db_path(conn)))
    install_card_temporal_function(peer)
    return peer


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
    predicate_form: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    resolution_status: str | None = None,
    fulfillment_assertion_id: int | None = None,
    reviewer: str | None = None,
    reviewed_at: str | None = None,
) -> int:
    ts = created_at or _now()
    obs = observed_at or ts
    pf = predicate_form or f"status({entity_id}, submitted, current)"
    if assertion_id is not None:
        conn.execute(
            "INSERT INTO assertions (id, entity_id, claim, confidence, predicate_form, "
            "review_status, review_notes, derivation_type, evidence_uris, superseded_by, "
            "valid_from, valid_until, resolution_status, fulfillment_assertion_id, "
            "entrenchment_score, created_at, updated_at, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, ?)",
            (
                assertion_id,
                entity_id,
                claim,
                confidence,
                pf,
                review_status,
                review_notes,
                derivation_type or "direct_observation",
                evidence_uris,
                superseded_by,
                valid_from,
                valid_until,
                resolution_status,
                fulfillment_assertion_id,
                ts,
                ts,
                obs,
            ),
        )
    else:
        cur = conn.execute(
            "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
            "review_status, review_notes, derivation_type, evidence_uris, superseded_by, "
            "valid_from, valid_until, resolution_status, fulfillment_assertion_id, "
            "entrenchment_score, created_at, updated_at, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, ?)",
            (
                entity_id,
                claim,
                confidence,
                pf,
                review_status,
                review_notes,
                derivation_type or "direct_observation",
                evidence_uris,
                superseded_by,
                valid_from,
                valid_until,
                resolution_status,
                fulfillment_assertion_id,
                ts,
                ts,
                obs,
            ),
        )
        assertion_id = int(cur.lastrowid or 0)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(assertions)")}
    if reviewer is not None and "reviewer" in cols:
        conn.execute(
            "UPDATE assertions SET reviewer = ? WHERE id = ?", (reviewer, assertion_id)
        )
    if reviewed_at is not None and "reviewed_at" in cols:
        conn.execute(
            "UPDATE assertions SET reviewed_at = ? WHERE id = ?",
            (reviewed_at, assertion_id),
        )
    conn.commit()
    return assertion_id


def _seed_account(
    conn: sqlite3.Connection,
    entity_id: str = "account:w4-fold",
    *,
    description: str = "Application not_submitted per letter.",
    attributes: dict[str, Any] | None = None,
) -> str:
    attrs = attributes if attributes is not None else {}
    insert_entity(
        conn,
        entity_id=entity_id,
        entity_type="account",
        name="W4 fold account",
        description=description,
        attributes=json.dumps(attrs),
    )
    return entity_id


def _eligible_submitted_assertion(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    created_at: str | None = None,
    review_status: str | None = None,
    review_notes: str | None = None,
    derivation_type: str = "direct_observation",
    evidence_uris: str = '["cortex://notes/evidence.md"]',
    confidence: str = "confirmed",
    token: str = "submitted",
    **kw: Any,
) -> int:
    pf = f"status({entity_id}, {token}, current)"
    return _insert_status_assertion(
        conn,
        entity_id=entity_id,
        claim="status claim",
        confidence=confidence,
        derivation_type=derivation_type,
        evidence_uris=evidence_uris,
        predicate_form=pf,
        review_status=review_status,
        review_notes=review_notes,
        created_at=created_at,
        **kw,
    )


def _osp_resolver(ref: str) -> UtteranceInfo | None:
    if ref == "agent-bus:operator#1":
        return UtteranceInfo(operator_seat=True)
    return None


def _attrs(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    raw = conn.execute(
        "SELECT attributes FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()["attributes"]
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def _fold_events(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    return [e for e in events if e[0] == "cortex.entity_state.folded"]


def _assert_entity_unchanged(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    field: str,
    before_attrs: dict[str, Any],
    before_wf: str | None,
) -> None:
    row = conn.execute(
        "SELECT attributes, workflow_state FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    after = _attrs(conn, entity_id)
    assert after.get(field) == before_attrs.get(field)
    assert row["workflow_state"] == before_wf


@pytest.fixture()
def recorded_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(signal: str, **payload: Any) -> None:
        events.append((signal, payload))

    monkeypatch.setattr("cortex_store.event_publisher.record", _capture)
    return events


from cortex_store.entity_state_closeout_scan import scan_live_w4_flagged_closeout_stats

# --- contention / concurrency / connection ---


def test_fold_operational_error_propagates_and_leaves_prior_value(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:op-err")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    before = _attrs(migrated_conn, eid)
    wrap = _ConnDelegate(migrated_conn)

    def boom(sql: str, *args: object) -> sqlite3.Cursor:
        if "BEGIN IMMEDIATE" in str(sql).upper():
            raise sqlite3.OperationalError("injected")
        return migrated_conn.execute(sql, *args)

    wrap.execute = boom  # type: ignore[method-assign]
    with pytest.raises(sqlite3.OperationalError):
        fold_claim(wrap, eid, aid, "application_submitted")  # type: ignore[arg-type]
    assert _attrs(migrated_conn, eid) == before
    assert not _fold_events(recorded_events)


def test_fold_own_begin_immediate_busy_propagates_operational_error(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:busy")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    before = _attrs(migrated_conn, eid)
    peer = _peer_conn(migrated_conn)
    peer.execute("BEGIN IMMEDIATE")
    try:
        migrated_conn.execute("PRAGMA busy_timeout=0")
        with pytest.raises(sqlite3.OperationalError):
            fold_claim(migrated_conn, eid, aid, "application_submitted")
    finally:
        peer.rollback()
        peer.close()
    assert _attrs(migrated_conn, eid) == before
    assert not _fold_events(recorded_events)
    assert migrated_conn.isolation_level in (None, "", "DEFERRED", "deferred")


def test_fold_different_fields_same_second_keep_both_provenance(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = "todo:w4-two-field"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="Two field",
        workflow_state="open",
        attributes=json.dumps({}),
    )
    ts = "2026-11-01T12:00:00+00:00"
    a1 = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="submitted",
        predicate_form=f"status({eid}, submitted, current)",
        created_at=ts,
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    a2 = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="in progress wf",
        predicate_form=f"status({eid}, in_progress, current)",
        created_at=ts,
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    r1 = fold_claim(migrated_conn, eid, a1, "application_submitted")
    r2 = fold_claim(migrated_conn, eid, a2, "workflow_state")
    assert r1.outcome == "folded"
    assert r2.outcome == "folded"
    attrs = _attrs(migrated_conn, eid)
    sp = attrs["state_provenance"]
    assert "application_submitted" in sp and "workflow_state" in sp


def test_fold_stale_state_hash_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:stale-attr")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    stale = state_sha256(migrated_conn, eid)
    conn2 = _peer_conn(migrated_conn)
    update_entity_impl(
        conn2, entity_id=eid, updates={"attributes": {"receipt_id": "x"}}
    )
    conn2.close()
    r = fold_claim(migrated_conn, eid, aid, "application_submitted", expected_state_sha256=stale)
    assert r.outcome == "refused" and r.reason == "stale_entity"


def test_fold_stale_workflow_state_hash_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = "todo:stale-wf"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="stale wf",
        workflow_state="open",
    )
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="done",
        predicate_form=f"status({eid}, done, current)",
    )
    stale = state_sha256(migrated_conn, eid)
    conn2 = _peer_conn(migrated_conn)
    update_entity_impl(conn2, entity_id=eid, updates={"workflow_state": "in_progress"})
    conn2.close()
    r = fold_claim(
        migrated_conn, eid, aid, "workflow_state", expected_state_sha256=stale
    )
    assert r.outcome == "refused" and r.reason == "stale_entity"


def test_fold_holds_write_lock_second_connection_supersede_busy(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:lock-hold")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    new_id = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-12-02T00:00:00+00:00"
    )
    peer = _peer_conn(migrated_conn)
    import cortex_store.entity_state_fold_tx as fold_tx_mod

    original = fold_tx_mod.update_entity_impl
    called = False

    def wrapper(conn: sqlite3.Connection, **kwargs: Any) -> dict[str, object]:
        nonlocal called
        called = True
        peer.execute("PRAGMA busy_timeout=0")
        with pytest.raises(sqlite3.OperationalError):
            peer.execute(
                "UPDATE assertions SET superseded_by = ? WHERE id = ?",
                (new_id, aid),
            )
        return original(conn, **kwargs)

    with patch.object(fold_tx_mod, "update_entity_impl", side_effect=wrapper):
        r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert called
    assert r.outcome == "folded"
    peer.close()


def test_fold_after_committed_supersede_refused_superseded(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:super-first")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    newer = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-12-03T00:00:00+00:00"
    )
    peer = _peer_conn(migrated_conn)
    peer.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?", (newer, aid)
    )
    peer.commit()
    peer.close()
    before = _attrs(migrated_conn, eid)
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "superseded"
    assert _attrs(migrated_conn, eid) == before


def test_fold_conn_in_transaction_refused_caller_txn_untouched(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:in-txn")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    migrated_conn.execute("BEGIN")
    migrated_conn.execute(
        "UPDATE entities SET name = name || 'x' WHERE id = ?", (eid,)
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "conn_in_transaction"
    migrated_conn.rollback()
    assert "x" not in conn_name(migrated_conn, eid)


def conn_name(conn: sqlite3.Connection, eid: str) -> str:
    return str(conn.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()[0])


def test_fold_restores_conn_isolation_level(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:iso")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    prior = migrated_conn.isolation_level
    fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert migrated_conn.isolation_level == prior


@pytest.mark.skipif(
    not hasattr(sqlite3.Connection, "autocommit"),
    reason="sqlite3.Connection.autocommit unavailable",
)
def test_fold_conn_nonlegacy_autocommit_refused_conn_isolation_unsupported(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:autocommit")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    migrated_conn.autocommit = True  # type: ignore[attr-defined]
    try:
        r = fold_claim(migrated_conn, eid, aid, "application_submitted")
        assert r.outcome == "refused" and r.reason == "conn_isolation_unsupported"
    finally:
        migrated_conn.autocommit = False  # type: ignore[attr-defined]


def test_fold_commit_failure_emits_nothing_and_keeps_prior_value(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:commit-fail")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    before = _attrs(migrated_conn, eid)
    wrap = _ConnDelegate(migrated_conn)
    seen = {"txn": False}

    def mark_execute(sql: str, *args: object) -> sqlite3.Cursor:
        if "BEGIN IMMEDIATE" in str(sql).upper():
            seen["txn"] = True
        return migrated_conn.execute(sql, *args)

    def boom_commit() -> None:
        if seen["txn"]:
            raise sqlite3.OperationalError("commit failed")
        migrated_conn.commit()

    wrap.execute = mark_execute  # type: ignore[method-assign]
    wrap.commit = boom_commit  # type: ignore[method-assign]
    with pytest.raises(sqlite3.OperationalError):
        fold_claim(wrap, eid, aid, "application_submitted")  # type: ignore[arg-type]
    assert _attrs(migrated_conn, eid) == before
    assert not _fold_events(recorded_events)


def test_fold_emit_failure_after_commit_keeps_fold_and_reports_emit_error(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eid = _seed_account(migrated_conn, "account:emit-fail")
    aid = _eligible_submitted_assertion(migrated_conn, eid)

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("emit failed")

    monkeypatch.setattr("cortex_store.event_publisher.record", boom)
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "folded"
    assert r.emit_error
    assert _attrs(migrated_conn, eid).get("application_submitted") is True


def test_fold_never_changes_description_bytes(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(
        migrated_conn,
        "account:desc-bytes",
        description="Payment not_submitted pending.",
    )
    desc_before = conn_execute_bytes(migrated_conn, eid)
    aid2 = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        review_status="flagged",
        review_notes=None,
    )
    fold_claim(migrated_conn, eid, aid2, "application_submitted")
    assert conn_execute_bytes(migrated_conn, eid) == desc_before


def conn_execute_bytes(conn: sqlite3.Connection, eid: str) -> str:
    return str(
        conn.execute("SELECT description FROM entities WHERE id=?", (eid,)).fetchone()[0]
    )


# --- binding ---


def test_fold_refuses_claim_for_other_entity(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:bind-a")
    other = _seed_account(migrated_conn, "account:bind-b")
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=other,
        claim="x",
        predicate_form=f"status({other}, submitted, current)",
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused"
    assert r.reason in {"claim_entity_mismatch", "predicate_mismatch"}


def test_fold_refuses_predicate_field_mismatch(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:pred-mismatch")
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="x",
        predicate_form=f"status({eid}, operational, current)",
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "predicate_mismatch"


def test_fold_value_is_parsed_from_predicate_form(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:bool-true")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    fold_claim(migrated_conn, eid, aid, "application_submitted")
    val = _attrs(migrated_conn, eid).get("application_submitted")
    assert val is True
    assert val != "true"


@pytest.mark.parametrize(
    "field",
    ["description", "predicate_form", "state_provenance", "name", "outcome", "urgency"],
)
def test_fold_refuses_denylisted_and_unlisted_fields(
    migrated_conn: sqlite3.Connection, field: str
) -> None:
    eid = _seed_account(migrated_conn, f"account:deny-{field[:4]}")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    r = fold_claim(migrated_conn, eid, aid, field)
    assert r.outcome == "refused" and r.reason == "field_not_allowed"


def test_fold_without_assertion_id_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:no-aid")
    r = fold_claim(migrated_conn, eid, None, "application_submitted")
    assert r.outcome == "refused" and r.reason == "assertion_id_required"


# --- gate ---


def test_inference_claim_is_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:inference")
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, derivation_type="inference"
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "inference_refused"


def test_believed_claim_with_evidence_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:believed")
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, confidence="believed"
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "not_confirmed"


def test_user_statement_tag_without_utterance_pointer_refused(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:us-no-ptr")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        derivation_type="user_statement",
        evidence_uris=None,
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "not_evidence_backed"


def test_osp_claim_with_resolvable_pointer_folds(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:osp-ok")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        derivation_type="user_statement",
        evidence_uris='["agent-bus:operator#1"]',
    )
    r = fold_claim(
        migrated_conn,
        eid,
        aid,
        "application_submitted",
        resolver=_osp_resolver,
    )
    assert r.outcome == "folded"


def test_osp_without_resolver_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:osp-no-res")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        derivation_type="user_statement",
        evidence_uris='["agent-bus:operator#1"]',
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "osp_unresolvable"


def test_fold_older_than_head_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:older-head")
    a_old = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00"
    )
    a_new = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-11-01T00:00:00+00:00"
    )
    fold_claim(migrated_conn, eid, a_new, "application_submitted")
    r = fold_claim(migrated_conn, eid, a_old, "application_submitted")
    assert r.outcome == "refused" and r.reason == "older_than_head"
    # operator_write head by set_at
    eid2 = _seed_account(migrated_conn, "account:op-head")
    a_claim = _eligible_submitted_assertion(
        migrated_conn, eid2, created_at="2026-09-01T00:00:00+00:00"
    )
    update_entity_impl(
        migrated_conn,
        entity_id=eid2,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "operator_write",
                            "ref": "agent-bus:operator#1",
                            "ref_kind": "utterance",
                            "value": True,
                            "set_by": "operator",
                            "set_at": "2026-11-01T00:00:00+00:00",
                        }
                    ]
                },
            }
        },
    )
    r2 = fold_claim(migrated_conn, eid2, a_claim, "application_submitted")
    assert r2.outcome == "refused" and r2.reason == "older_than_head"


def test_fold_older_than_operator_write_head_space_created_at(
    migrated_conn: sqlite3.Connection,
) -> None:
    """Space-format ``created_at`` must not sort before T…Z ``set_at`` on the same UTC day."""
    eid = _seed_account(migrated_conn, "account:op-instant-key")
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "operator_write",
                            "ref": "agent-bus:operator#1",
                            "ref_kind": "utterance",
                            "value": True,
                            "set_by": "operator",
                            "set_at": "2026-11-08T12:00:00Z",
                        }
                    ]
                },
            }
        },
    )
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="submitted later",
        predicate_form=f"status({eid}, submitted, current)",
        created_at="2026-11-08 13:00:00",
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "folded"


def test_fold_operator_write_garbage_set_at_refused_ordering_unparseable(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:garbage-set-at")
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "operator_write",
                            "ref": "agent-bus:operator#1",
                            "ref_kind": "utterance",
                            "value": True,
                            "set_by": "operator",
                            "set_at": "garbage",
                        }
                    ]
                },
            }
        },
    )
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "ordering_unparseable"


def test_field_state_empty_created_at_claim_ineligible(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:empty-created-at")
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="empty ts",
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    migrated_conn.execute("UPDATE assertions SET created_at = ? WHERE id = ?", ("", aid))
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.newer_claim_id is None


def test_reverse_fold_duplicate_provenance_id_refused(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:dup-prov")
    sp = {
        "application_submitted": [
            {
                "provenance_id": "application_submitted:1",
                "kind": "fold",
                "ref": "assertion:1",
                "ref_kind": "assertion",
                "value": True,
                "set_by": "fold",
                "set_at": _now(),
                "assertion_id": 1,
                "previous_present": False,
            },
            {
                "provenance_id": "application_submitted:1",
                "kind": "fold",
                "ref": "assertion:2",
                "ref_kind": "assertion",
                "value": True,
                "set_by": "fold",
                "set_at": _now(),
                "assertion_id": 2,
                "previous_present": False,
            },
        ]
    }
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={"attributes": {"application_submitted": True, "state_provenance": sp}},
    )
    r = reverse_fold(migrated_conn, eid, "application_submitted", "application_submitted:1")
    assert r.outcome == "refused" and r.reason == "reverse_conflict"


def test_osp_resolver_raises_maps_to_unresolvable(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:osp-boom")

    def _boom(_ref: str) -> UtteranceInfo:
        raise RuntimeError("resolver down")

    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="op said",
        derivation_type="user_statement",
        evidence_uris='["agent-bus:22652#1"]',
    )
    r = fold_claim(
        migrated_conn, eid, aid, "application_submitted", resolver=_boom
    )
    assert r.outcome == "refused" and r.reason == "osp_unresolvable"


def test_fold_with_no_head_has_no_ordering_refusal(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:no-head")
    a1 = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00"
    )
    a2 = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-11-01T00:00:00+00:00"
    )
    assert fold_claim(migrated_conn, eid, a1, "application_submitted").outcome == "folded"
    assert fold_claim(migrated_conn, eid, a2, "application_submitted").outcome == "folded"


def test_user_statement_with_chat_url_evidence_refused_not_evidence_backed(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:chat-url")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        derivation_type="user_statement",
        evidence_uris='["https://chat.example.com/thread"]',
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted", resolver=_osp_resolver)
    assert r.outcome == "refused" and r.reason == "not_evidence_backed"


def test_osp_claim_not_confirmed_refused_not_confirmed(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:osp-unconf")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        confidence="believed",
        derivation_type="user_statement",
        evidence_uris='["agent-bus:operator#1"]',
    )
    r = fold_claim(
        migrated_conn, eid, aid, "application_submitted", resolver=_osp_resolver
    )
    assert r.outcome == "refused" and r.reason == "not_confirmed"


def test_fold_gate_is_subset_of_w2_evidence_backed(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:gate-subset")
    variants = [
        ("direct_observation", "confirmed", '["cortex://x"]'),
        ("user_statement", "confirmed", None),
        ("inference", "confirmed", '["cortex://x"]'),
        (None, "confirmed", '["cortex://x"]'),
    ]
    for deriv, conf, ev in variants:
        aid = _insert_status_assertion(
            migrated_conn,
            entity_id=eid,
            claim="v",
            confidence=conf,
            derivation_type=deriv,
            evidence_uris=ev,
            predicate_form=f"status({eid}, submitted, current)",
        )
        row = dict(
            migrated_conn.execute("SELECT * FROM assertions WHERE id=?", (aid,)).fetchone()
        )
        w4 = is_evidence_backed_w4(row)
        w2 = _evidence_backed(row)
        if w4:
            assert w2


def test_fold_head_claim_row_missing_refused_head_claim_missing(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:missing-head-claim")
    ghost_id = 9_999_991
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "fold",
                            "ref": f"assertion:{ghost_id}",
                            "ref_kind": "assertion",
                            "value": True,
                            "set_by": "fold",
                            "set_at": _now(),
                            "assertion_id": ghost_id,
                        }
                    ]
                },
            }
        },
    )
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-12-01T00:00:00+00:00"
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "head_claim_missing"


# --- concern ---


def test_concern_head_row_only_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:concern-head")
    migrated_conn.execute(
        "INSERT INTO concern_head (concern_id, revision, record_ref) VALUES (?, 1, ?)",
        (eid, "rec:test"),
    )
    migrated_conn.commit()
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "concern_refused"


def test_concern_type_only_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = "concern:w4-only"
    insert_entity(migrated_conn, entity_id=eid, entity_type="concern", name="C")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "refused" and r.reason == "concern_refused"


# --- flag rule ---


def _withhold_assert(
    conn: sqlite3.Connection,
    eid: str,
    aid: int,
    field: str = "application_submitted",
) -> FoldResult:
    before_attrs = _attrs(conn, eid)
    before_wf = conn.execute(
        "SELECT workflow_state FROM entities WHERE id=?", (eid,)
    ).fetchone()["workflow_state"]
    r = fold_claim(conn, eid, aid, field)
    _assert_entity_unchanged(
        conn, eid, field=field, before_attrs=before_attrs, before_wf=before_wf
    )
    return r


def test_flagged_reasonless_evidence_backed_claim_folds_with_marker(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:flag-reasonless")
    desc_before = conn_execute_bytes(migrated_conn, eid)
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, review_status="flagged", review_notes=None
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "folded"
    assert r.marker == "flagged, no reason"
    attrs = _attrs(migrated_conn, eid)
    assert attrs.get("application_submitted") is True
    rec = attrs["state_provenance"]["application_submitted"][-1]
    assert rec["ref"] == f"assertion:{aid}"
    assert conn_execute_bytes(migrated_conn, eid) == desc_before
    assert len(_fold_events(recorded_events)) == 1


def test_flagged_predicate_normalize_claim_folds_with_marker(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:flag-pred-norm")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        review_status="flagged",
        review_notes="predicate normalize: invention: detail",
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "folded"
    assert r.marker == "flagged, no reason"


def test_flagged_predicate_normalize_with_reviewer_and_reviewed_at_folds_with_marker(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:flag-pred-rev")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        review_status="flagged",
        review_notes="predicate normalize: x",
        reviewer="operator",
        reviewed_at=_now(),
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "folded"


def test_flagged_with_reviewer_and_non_normalize_notes_withheld_reason_is_raw_notes(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:flag-withhold-notes")
    notes = "human reason retained verbatim"
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        review_status="flagged",
        review_notes=notes,
        reviewer="operator",
        reviewed_at=_now(),
    )
    r = _withhold_assert(migrated_conn, eid, aid)
    assert r.outcome == "withheld" and r.reason == notes
    assert not _fold_events(recorded_events)


@pytest.mark.parametrize(
    "notes",
    [None, "", "   "],
)
@pytest.mark.parametrize(
    "reviewer,reviewed_at",
    [
        (None, None),
        ("op", None),
        (None, _now()),
        ("op", _now()),
    ],
)
def test_flagged_reasonless_with_reviewer_or_reviewed_at_folds_with_marker(
    migrated_conn: sqlite3.Connection,
    notes: str | None,
    reviewer: str | None,
    reviewed_at: str | None,
) -> None:
    eid = _seed_account(migrated_conn, f"account:flag-rider-{hash((notes, reviewer)) & 0xffff}")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        review_status="flagged",
        review_notes=notes,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "folded"


def test_flagged_contradiction_chunk_first_withheld_no_write_returns_reason(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:flag-contradict")
    notes = "contradiction: values disagree"
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, review_status="flagged", review_notes=notes
    )
    r = _withhold_assert(migrated_conn, eid, aid)
    assert r.outcome == "withheld" and r.reason == notes
    assert not _fold_events(recorded_events)


def test_flagged_cross_entity_contradiction_detected_withheld_reviewer_null_reason_is_raw_notes(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:flag-cross")
    notes = "Cross-entity contradiction detected: mismatch"
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, review_status="flagged", review_notes=notes
    )
    r = _withhold_assert(migrated_conn, eid, aid)
    assert r.outcome == "withheld" and r.reason == notes
    assert not _fold_events(recorded_events)


def test_flagged_semantic_contradiction_withheld_reviewer_null_reason_is_raw_notes(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:flag-semantic")
    notes = "Semantic contradiction: incompatible"
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, review_status="flagged", review_notes=notes
    )
    r = _withhold_assert(migrated_conn, eid, aid)
    assert r.outcome == "withheld" and r.reason == notes
    assert not _fold_events(recorded_events)


@pytest.mark.parametrize(
    "phrase",
    list(CONTRADICTION_PREFIXES),
)
@pytest.mark.parametrize("variant", ["upper", "space"])
def test_flagged_contradiction_prefix_is_case_insensitive_after_trim(
    migrated_conn: sqlite3.Connection, phrase: str, variant: str
) -> None:
    eid = _seed_account(migrated_conn, f"account:flag-ci-{hash((phrase, variant)) & 9999}")
    body = phrase if variant == "upper" else f"  {phrase} detail"
    notes = body.upper() if variant == "upper" else body
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, review_status="flagged", review_notes=notes
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "withheld"


@pytest.mark.parametrize("phrase", list(CONTRADICTION_PREFIXES))
def test_flagged_contradiction_phrasing_after_predicate_normalize_withheld(
    migrated_conn: sqlite3.Connection, phrase: str
) -> None:
    eid = _seed_account(migrated_conn, f"account:flag-pn-{hash(phrase) & 9999}")
    notes = f"predicate normalize: x; {phrase} y"
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, review_status="flagged", review_notes=notes
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "withheld"


def test_flagged_contradict_word_not_at_chunk_start_folds_with_marker(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:flag-substr")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        review_status="flagged",
        review_notes="note contradicts elsewhere",
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "folded"
    assert r.marker == "flagged, no reason"


def test_unflagged_claim_with_semantic_contradiction_note_not_withheld(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:unflag-sem")
    aid = _eligible_submitted_assertion(
        migrated_conn,
        eid,
        review_status=None,
        review_notes="Semantic contradiction: only prose",
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "folded"


def test_flag_detail_carries_raw_notes_and_token(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:flag-detail")
    notes = "predicate normalize: invention: widget"
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, review_status="flagged", review_notes=notes
    )
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.flag_detail is not None
    assert r.flag_detail.get("notes") == notes
    assert "token" in r.flag_detail


# --- reverse ---


def test_reverse_restores_previous_value(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:rev-basic", attributes={"application_submitted": False})
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    fr = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert fr.outcome == "folded"
    pid = fr.provenance_id
    assert pid
    rr = reverse_fold(migrated_conn, eid, "application_submitted", pid)
    assert rr.outcome == "reversed"
    assert _attrs(migrated_conn, eid).get("application_submitted") is False
    assert len(_fold_events(recorded_events)) == 2


def test_fold_operator_edit_reverse_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:rev-op-edit")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    fr = fold_claim(migrated_conn, eid, aid, "application_submitted")
    pid = fr.provenance_id or ""
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={"attributes": {"application_submitted": False}},
    )
    rr = reverse_fold(migrated_conn, eid, "application_submitted", pid)
    assert rr.outcome == "refused" and rr.reason == "reverse_conflict"


def test_fold_twice_reverse_first_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:rev-twice")
    a1 = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00")
    a2 = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-11-01T00:00:00+00:00")
    p1 = fold_claim(migrated_conn, eid, a1, "application_submitted").provenance_id
    p2 = fold_claim(migrated_conn, eid, a2, "application_submitted").provenance_id
    assert p1 and p2
    assert reverse_fold(migrated_conn, eid, "application_submitted", p1).outcome == "refused"


def test_fold_new_key_reverse_leaves_null_tombstone_and_field_state_reads_absent(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:rev-tomb")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    fr = fold_claim(migrated_conn, eid, aid, "application_submitted")
    pid = fr.provenance_id or ""
    reverse_fold(migrated_conn, eid, "application_submitted", pid)
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.value is None
    assert fs.present is False


def test_reverse_twice_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:rev-dup", attributes={"application_submitted": False})
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    fr = fold_claim(migrated_conn, eid, aid, "application_submitted")
    pid = fr.provenance_id or ""
    assert reverse_fold(migrated_conn, eid, "application_submitted", pid).outcome == "reversed"
    assert reverse_fold(migrated_conn, eid, "application_submitted", pid).outcome == "refused"


def test_refold_same_claim_same_value_is_noop_no_event(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:noop")
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    assert fold_claim(migrated_conn, eid, aid, "application_submitted").outcome == "folded"
    events_before = len(_fold_events(recorded_events))
    r = fold_claim(migrated_conn, eid, aid, "application_submitted")
    assert r.outcome == "noop"
    assert len(_fold_events(recorded_events)) == events_before


def test_fold_other_claim_same_value_appends_record_advances_head_emits_one_event(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = _seed_account(migrated_conn, "account:other-claim")
    a1 = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00")
    a2 = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-11-02T00:00:00+00:00")
    fold_claim(migrated_conn, eid, a1, "application_submitted")
    events_before = len(_fold_events(recorded_events))
    r = fold_claim(migrated_conn, eid, a2, "application_submitted")
    assert r.outcome == "folded"
    assert len(_fold_events(recorded_events)) == events_before + 1
    recs = _attrs(migrated_conn, eid)["state_provenance"]["application_submitted"]
    assert len(recs) == 2


def test_head_after_reversal_is_prior_fold_and_verified(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:head-after-rev", attributes={"application_submitted": False})
    a1 = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00", token="submitted"
    )
    a2 = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-11-02T00:00:00+00:00", token="submitted"
    )
    # first fold from false -> true, second also true, reverse second
    p1 = fold_claim(migrated_conn, eid, a1, "application_submitted").provenance_id
    p2 = fold_claim(migrated_conn, eid, a2, "application_submitted").provenance_id
    reverse_fold(migrated_conn, eid, "application_submitted", p2 or "")
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.verification == "verified"
    assert fs.value is True
    head = _attrs(migrated_conn, eid)["state_provenance"]["application_submitted"]
    assert head[-1]["kind"] == "reversal"
    assert head[-3]["provenance_id"] == p1


def test_reversal_record_is_never_head(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:rev-never-head", attributes={"application_submitted": False})
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    pid = fold_claim(migrated_conn, eid, aid, "application_submitted").provenance_id or ""
    reverse_fold(migrated_conn, eid, "application_submitted", pid)
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.head_ref is None
    assert fs.verification == "unverified"


def test_refold_after_reversal_refused(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:refold-rev", attributes={"application_submitted": False})
    a1 = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00")
    a2 = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-11-01T00:00:00+00:00")
    pid = fold_claim(migrated_conn, eid, a1, "application_submitted").provenance_id or ""
    reverse_fold(migrated_conn, eid, "application_submitted", pid)
    r_old = fold_claim(migrated_conn, eid, a1, "application_submitted")
    assert r_old.outcome == "refused" and r_old.reason == "refold_after_reversal"
    assert fold_claim(migrated_conn, eid, a2, "application_submitted").outcome == "folded"


def test_reversal_record_shape(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:rev-shape", attributes={"application_submitted": False})
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    pid = fold_claim(migrated_conn, eid, aid, "application_submitted").provenance_id or ""
    reverse_fold(migrated_conn, eid, "application_submitted", pid)
    rev = _attrs(migrated_conn, eid)["state_provenance"]["application_submitted"][-1]
    assert rev["kind"] == "reversal"
    assert rev["reverses"] == pid
    assert rev["ref"] == f"provenance:{pid}"
    assert rev["ref_kind"] == "provenance"


def test_reverse_operator_write_head_refused_reverse_conflict(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:rev-op-head")
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "operator_write",
                            "ref": "agent-bus:operator#1",
                            "ref_kind": "utterance",
                            "value": True,
                            "set_by": "operator",
                            "set_at": _now(),
                        }
                    ]
                },
            }
        },
    )
    r = reverse_fold(migrated_conn, eid, "application_submitted", "application_submitted:1")
    assert r.outcome == "refused" and r.reason == "reverse_conflict"


def test_provenance_id_is_list_position_and_never_reused(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:prov-id")
    a1 = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00")
    a2 = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-11-01T00:00:00+00:00")
    p1 = fold_claim(migrated_conn, eid, a1, "application_submitted").provenance_id
    p2 = fold_claim(migrated_conn, eid, a2, "application_submitted").provenance_id
    assert p1 == "application_submitted:1"
    assert p2 == "application_submitted:2"


# --- workflow_state ---


def test_workflow_state_done_without_live_verify_attrs_gate_refused_no_event(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = "todo:lv-missing"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="live verify todo",
        workflow_state="open",
        attributes=json.dumps({"live_verify_required": True}),
    )
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="done",
        predicate_form=f"status({eid}, done, current)",
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    r = fold_claim(migrated_conn, eid, aid, "workflow_state")
    assert r.outcome == "refused" and r.reason.startswith("gate_refused:")
    assert not _fold_events(recorded_events)


def test_workflow_state_condition_entity_refused(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = "condition:w4-fold"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="condition",
        name="condition",
        workflow_state="open",
    )
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="done",
        predicate_form=f"status({eid}, done, current)",
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    r = fold_claim(migrated_conn, eid, aid, "workflow_state")
    assert r.outcome == "refused" and r.reason.startswith("gate_refused:")
    assert not _fold_events(recorded_events)


def test_workflow_state_fold_event_count(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = "todo:wf-events"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="wf fold",
        workflow_state="open",
    )
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="in_progress",
        predicate_form=f"status({eid}, in_progress, current)",
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    r = fold_claim(migrated_conn, eid, aid, "workflow_state")
    assert r.outcome == "folded"
    folded = _fold_events(recorded_events)
    assert len(folded) == 1


def test_workflow_state_todo_done_emits_fold_and_closure_gap(
    migrated_conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    eid = "todo:wf-done-gap"
    insert_entity(
        migrated_conn,
        entity_id=eid,
        entity_type="todo",
        name="done gap todo",
        workflow_state="open",
        attributes=json.dumps({"live_verify_required": False}),
    )
    aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="done",
        predicate_form=f"status({eid}, done, current)",
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    r = fold_claim(migrated_conn, eid, aid, "workflow_state")
    assert r.outcome == "folded"
    assert r.events_emitted == 2
    signals = [e[0] for e in recorded_events]
    assert "cortex.entity_state.folded" in signals


def test_reverse_application_submitted_previous_present_false_tombstone(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:rev-tombstone", attributes={})
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    pid = fold_claim(migrated_conn, eid, aid, "application_submitted").provenance_id or ""
    r = reverse_fold(migrated_conn, eid, "application_submitted", pid)
    assert r.outcome == "reversed"
    attrs = _attrs(migrated_conn, eid)
    assert attrs.get("application_submitted") is None
    assert field_state(migrated_conn, eid, "application_submitted").present is False


# --- field_state read rule ---


def test_field_state_unverified_loses_to_newer_eligible_claim(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:fs-unver")
    a_new = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-11-03T00:00:00+00:00"
    )
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.verification == "unverified"
    assert fs.newer_claim_id == a_new


def test_field_state_verified_with_newer_claim_returns_both(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:fs-ver-both", attributes={"application_submitted": False})
    a_old = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00")
    a_new = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-11-05T00:00:00+00:00")
    fold_claim(migrated_conn, eid, a_old, "application_submitted")
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.verification == "verified"
    assert fs.newer_claim_id == a_new


def test_field_state_skips_reversed_claim(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:fs-skip-rev", attributes={"application_submitted": False})
    a_old = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00")
    a_new = _eligible_submitted_assertion(migrated_conn, eid, created_at="2026-11-05T00:00:00+00:00")
    pid = fold_claim(migrated_conn, eid, a_old, "application_submitted").provenance_id or ""
    reverse_fold(migrated_conn, eid, "application_submitted", pid)
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.newer_claim_id == a_new


def test_field_state_skips_withheld_claim(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:fs-skip-wh")
    a_eligible = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-10-01T00:00:00+00:00"
    )
    _eligible_submitted_assertion(
        migrated_conn,
        eid,
        created_at="2026-11-06T00:00:00+00:00",
        review_status="flagged",
        review_notes="contradiction: blocked",
    )
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.newer_claim_id == a_eligible


def test_field_state_duplicate_provenance_id_unverified(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:fs-dup-pid")
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "fold",
                            "ref": "assertion:1",
                            "ref_kind": "assertion",
                            "value": True,
                            "set_by": "fold",
                            "set_at": _now(),
                            "assertion_id": 1,
                        },
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "fold",
                            "ref": "assertion:2",
                            "ref_kind": "assertion",
                            "value": True,
                            "set_by": "fold",
                            "set_at": _now(),
                            "assertion_id": 2,
                        },
                    ]
                },
            }
        },
    )
    assert field_state(migrated_conn, eid, "application_submitted").verification == "unverified"


def test_field_state_head_claim_missing_counts_newest_as_newer(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:fs-missing-head")
    ghost = 8_888_881
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "fold",
                            "ref": f"assertion:{ghost}",
                            "ref_kind": "assertion",
                            "value": True,
                            "set_by": "fold",
                            "set_at": _now(),
                            "assertion_id": ghost,
                        }
                    ]
                },
            }
        },
    )
    a_new = _eligible_submitted_assertion(
        migrated_conn, eid, created_at="2026-12-01T00:00:00+00:00"
    )
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.verification == "verified"
    assert fs.newer_claim_id == a_new


def test_field_state_junk_ref_operator_record_reads_verified_residual(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:fs-junk-ref")
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "operator_write",
                            "ref": "not-a-real-pointer",
                            "ref_kind": "utterance",
                            "value": True,
                            "set_by": "operator",
                            "set_at": _now(),
                        }
                    ]
                },
            }
        },
    )
    assert field_state(migrated_conn, eid, "application_submitted").verification == "verified"


def test_field_state_no_head_newest_eligible_claim_counts_as_newer(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:fs-no-head-newer")
    a_new = _eligible_submitted_assertion(migrated_conn, eid)
    fs = field_state(migrated_conn, eid, "application_submitted")
    assert fs.newer_claim_id == a_new


def test_operator_write_record_with_value_ref_verifies(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:op-write-ver")
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "operator_write",
                            "ref": "agent-bus:operator#1",
                            "ref_kind": "utterance",
                            "value": True,
                            "set_by": "operator",
                            "set_at": _now(),
                        }
                    ]
                },
            }
        },
    )
    assert field_state(migrated_conn, eid, "application_submitted").verification == "verified"


def test_operator_write_without_record_stays_unverified(migrated_conn: sqlite3.Connection) -> None:
    eid = _seed_account(migrated_conn, "account:op-no-record")
    update_entity_impl(
        migrated_conn, entity_id=eid, updates={"attributes": {"application_submitted": True}}
    )
    assert field_state(migrated_conn, eid, "application_submitted").verification == "unverified"


def test_operator_record_missing_provenance_id_or_value_stays_unverified(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:op-missing-parts")
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "kind": "operator_write",
                            "ref": "agent-bus:operator#1",
                            "ref_kind": "utterance",
                            "set_by": "operator",
                            "set_at": _now(),
                        }
                    ]
                },
            }
        },
    )
    assert field_state(migrated_conn, eid, "application_submitted").verification == "unverified"


def test_field_state_verified_is_not_path_proof_aba_residual(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(migrated_conn, "account:fs-aba")
    head_aid = _insert_status_assertion(
        migrated_conn,
        entity_id=eid,
        claim="head",
        assertion_id=10,
        predicate_form=f"status({eid}, submitted, current)",
    )
    update_entity_impl(
        migrated_conn,
        entity_id=eid,
        updates={
            "attributes": {
                "application_submitted": True,
                "state_provenance": {
                    "application_submitted": [
                        {
                            "provenance_id": "application_submitted:1",
                            "kind": "fold",
                            "ref": f"assertion:{head_aid}",
                            "ref_kind": "assertion",
                            "value": True,
                            "set_by": "fold",
                            "set_at": _now(),
                            "assertion_id": head_aid,
                        }
                    ]
                },
            }
        },
    )
    update_entity_impl(
        migrated_conn, entity_id=eid, updates={"attributes": {"application_submitted": False}}
    )
    update_entity_impl(
        migrated_conn, entity_id=eid, updates={"attributes": {"application_submitted": True}}
    )
    assert field_state(migrated_conn, eid, "application_submitted").verification == "verified"


# --- W2 compatibility ---


def test_w2_scanner_reports_f1_row_after_fold_while_summary_negation_remains(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(
        migrated_conn,
        "account:w2-f1-fold",
        description="Payment not_submitted pending review.",
    )
    aid = _eligible_submitted_assertion(
        migrated_conn, eid, review_status="flagged", review_notes=None
    )
    assert fold_claim(migrated_conn, eid, aid, "application_submitted").outcome == "folded"
    rows = [
        r
        for r in scan_entity_state_drift(conn=migrated_conn).rows
        if r.entity_id == eid and r.drift_class == "F1"
    ]
    assert rows
    assert "application_submitted" in (rows[0].right_cite or "")


def test_w2_f1_reads_null_tombstone_as_not_affirmative(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = _seed_account(
        migrated_conn,
        "account:w2-null-tomb",
        description="Status not_submitted on file.",
    )
    aid = _eligible_submitted_assertion(migrated_conn, eid)
    pid = fold_claim(migrated_conn, eid, aid, "application_submitted").provenance_id or ""
    reverse_fold(migrated_conn, eid, "application_submitted", pid)
    rows = [
        r
        for r in scan_entity_state_drift(conn=migrated_conn).rows
        if r.entity_id == eid and r.drift_class == "F1"
    ]
    assert not rows


def test_scan_live_w4_flagged_closeout_stats_fixture(migrated_conn: sqlite3.Connection) -> None:
    f3_eid = "plan:scan-f3-fixture"
    insert_entity(
        migrated_conn,
        entity_id=f3_eid,
        entity_type="plan",
        name="scan f3 plan",
        description="staging",
    )
    _insert_status_assertion(
        migrated_conn,
        entity_id=f3_eid,
        claim="served staging",
        confidence="believed",
        created_at="2026-10-20T00:00:00+00:00",
    )
    _insert_status_assertion(
        migrated_conn,
        entity_id=f3_eid,
        claim="newer flagged operational",
        confidence="confirmed",
        review_status="flagged",
        review_notes="predicate normalize: detail",
        created_at="2026-11-05T00:00:00+00:00",
        derivation_type="direct_observation",
        evidence_uris='["cortex://notes/evidence.md"]',
        predicate_form=f"status({f3_eid}, submitted, current)",
    )
    _insert_status_assertion(
        migrated_conn,
        entity_id=f3_eid,
        claim="withheld chunk",
        confidence="confirmed",
        review_status="flagged",
        review_notes="contradiction: stale",
        created_at="2026-11-06T00:00:00+00:00",
        derivation_type="direct_observation",
        evidence_uris='["cortex://notes/evidence.md"]',
    )
    exclude_eid = "account:scan-gate-not-f3"
    insert_entity(
        migrated_conn,
        entity_id=exclude_eid,
        entity_type="account",
        name="scan exclude",
        description="flagged, gate-clear, not F3 newer",
    )
    _insert_status_assertion(
        migrated_conn,
        entity_id=exclude_eid,
        claim="flagged foldable not f3",
        confidence="confirmed",
        review_status="flagged",
        review_notes="predicate normalize: excluded from f3 subset",
        created_at="2026-11-07T00:00:00+00:00",
        derivation_type="direct_observation",
        evidence_uris='["cortex://notes/evidence.md"]',
        predicate_form=f"status({exclude_eid}, submitted, current)",
    )
    drift_rows = [
        r for r in scan_entity_state_drift(conn=migrated_conn).rows if r.drift_class == "F3"
    ]
    assert any(r.right_cite.startswith("assertion:") for r in drift_rows)
    stats = scan_live_w4_flagged_closeout_stats(migrated_conn)
    assert stats["contradiction_chunk_flagged"] >= 1
    assert stats["f3_rows"] >= 1
    assert stats["flagged_foldable"] == 1
    assert stats["predicate_normalize"] == 1
    assert (
        stats["reasonless"] + stats["predicate_normalize"] + stats["other"]
        == stats["flagged_foldable"]
    )
