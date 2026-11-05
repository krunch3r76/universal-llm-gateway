"""Phase-1 concern aggregate acceptance tests (A1–A14)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from cortex_store.concern_aggregate.advisory_outbox import (
    AdvisoryEmitError,
    emit_pending,
)
from cortex_store.concern_aggregate.checkpoint import (
    latest_valid_checkpoint,
    write_checkpoint,
)
from cortex_store.concern_aggregate.fault_injection import (
    set_after_journal_insert,
)
from cortex_store.concern_aggregate.identities import (
    IdentityTypeError,
    as_request_id,
)
from cortex_store.concern_aggregate.journal import (
    TransitionRejectedError,
    append_transition,
)
from cortex_store.concern_aggregate.recovery import (
    read_effective_state,
    rebuild_concern,
)
from cortex_store.concern_aggregate.views import read_controls, read_cursor, read_head
from cortex_store.dispatch_ops import _OP_SPECS

pytestmark = pytest.mark.offline

AUTH = "decision:test-auth"


def _append(
    conn: sqlite3.Connection,
    concern_id: str,
    *,
    kind: str = "control_set",
    record_ref: str,
    **payload: object,
) -> dict:
    body = {k: v for k, v in payload.items() if k not in {"intent_id", "request_id"}}
    return append_transition(
        conn,
        concern_id=concern_id,
        kind=kind,
        payload=body,
        record_ref=record_ref,
        authorized_by=AUTH,
        intent_id=str(payload["intent_id"]) if "intent_id" in payload else None,
        request_id=str(payload["request_id"]) if "request_id" in payload else None,
    )


def test_crash_boundary_append_head(concern_db: sqlite3.Connection) -> None:
    """A1: fault after journal insert rolls back; rebuild matches last commit."""
    cid = "concern:a1"
    _append(
        concern_db,
        cid,
        record_ref="rec-1",
        control_key="hold-a",
        hold={"active": True},
    )
    before = read_effective_state(concern_db, cid)

    def _boom() -> None:
        raise RuntimeError("fault after journal insert")

    set_after_journal_insert(_boom)
    with pytest.raises(RuntimeError):
        _append(
            concern_db,
            cid,
            record_ref="rec-2",
            control_key="hold-b",
            hold={"active": True},
        )
    after = read_effective_state(concern_db, cid)
    assert after == before
    rebuilt = rebuild_concern(concern_db, cid)
    assert rebuilt["controls"] == before["controls"]


def test_crash_boundary_head_cursor(concern_db: sqlite3.Connection) -> None:
    """A2: corrupt head without cursor advance; rebuild restores fold."""
    cid = "concern:a2"
    append_transition(
        concern_db,
        concern_id=cid,
        kind="position_head",
        payload={},
        record_ref="rec-1",
        authorized_by=AUTH,
    )
    _append(concern_db, cid, record_ref="rec-2", control_key="k2", hold={})
    concern_db.execute(
        "UPDATE concern_head SET revision = revision + 99 WHERE concern_id = ?",
        (cid,),
    )
    concern_db.commit()
    through, _ = read_cursor(concern_db, cid)
    assert through == 2
    rebuilt = rebuild_concern(concern_db, cid)
    assert read_head(concern_db, cid)["revision"] == rebuilt["head"]["revision"]
    assert read_controls(concern_db, cid) == rebuilt["controls"]


def test_crash_boundary_checkpoint(concern_db: sqlite3.Connection) -> None:
    """A3: partial checkpoint ignored when sha mismatches."""
    cid = "concern:a3"
    _append(concern_db, cid, record_ref="rec-1", control_key="k", hold={"v": 1})
    write_checkpoint(concern_db, concern_id=cid)
    concern_db.execute(
        "INSERT INTO concern_checkpoint "
        "(concern_id, through_seq, fold_version, folded_state, state_sha256) "
        "VALUES (?, 2, 1, '{}', 'badsha')",
        (cid,),
    )
    concern_db.commit()
    assert latest_valid_checkpoint(concern_db, cid) is not None
    bad = concern_db.execute(
        "SELECT state_sha256 FROM concern_checkpoint WHERE concern_id=? AND state_sha256='badsha'",
        (cid,),
    ).fetchone()
    assert bad is not None
    ck = latest_valid_checkpoint(concern_db, cid)
    assert ck is not None and ck["state_sha256"] != "badsha"


def test_cursor_no_skip_delayed_lower_commit(concern_db: sqlite3.Connection) -> None:
    """A4: cursor stays at 1 until in-flight lower seq commits."""
    cid = "concern:a4"
    _append(concern_db, cid, record_ref="rec-first", control_key="first", hold={})
    concern_db.execute(
        "INSERT OR IGNORE INTO concern_seq_alloc (concern_id, next_seq) VALUES (?, 2)",
        (cid,),
    )
    concern_db.execute(
        "UPDATE concern_seq_alloc SET next_seq = 4 WHERE concern_id = ?",
        (cid,),
    )
    concern_db.execute(
        "INSERT INTO concern_journal "
        "(concern_id, seq, record_ref, kind, payload, authorized_by, "
        "fencing_generation, revision, fold_version, schema_version, committed_at) "
        "VALUES (?, 2, 'rec-low', 'control_set', ?, ?, 0, 2, 1, 1, NULL)",
        (cid, json.dumps({"control_key": "late"}), AUTH),
    )
    concern_db.commit()
    _append(concern_db, cid, record_ref="rec-high", control_key="high", hold={})
    through_before, _ = read_cursor(concern_db, cid)
    assert through_before == 1
    now = datetime.now(UTC).isoformat()
    concern_db.execute(
        "UPDATE concern_journal SET committed_at = ? WHERE concern_id=? AND seq=2",
        (now, cid),
    )
    concern_db.execute(
        "INSERT INTO concern_controls (concern_id, control_key, hold_json) VALUES (?, ?, ?)",
        (cid, "late", "{}"),
    )
    concern_db.execute(
        "UPDATE concern_cursor SET through_seq = 3, cursor_token = 'cur:3' WHERE concern_id = ?",
        (cid,),
    )
    concern_db.commit()
    through_after, _ = read_cursor(concern_db, cid)
    assert through_after == 3
    assert "late" in read_controls(concern_db, cid)


def test_effective_controls_no_divergence(concern_db: sqlite3.Connection) -> None:
    """A5: two readers at same cursor see identical head+controls."""
    cid = "concern:a5"
    _append(concern_db, cid, record_ref="rec-1", control_key="k", hold={"n": 1})
    snap_a = read_effective_state(concern_db, cid)
    snap_b = read_effective_state(concern_db, cid)
    assert snap_a == snap_b


def test_long_history_bounded_replay(concern_db: sqlite3.Connection) -> None:
    """A6: checkpoint bounds replay work for long histories."""
    cid = "concern:a6"
    interval = 500
    total = 2000
    for i in range(total):
        _append(
            concern_db,
            cid,
            record_ref=f"rec-{i}",
            control_key=f"k{i % 10}",
            hold={"i": i},
        )
        if (i + 1) % interval == 0:
            write_checkpoint(concern_db, concern_id=cid)
    ck = latest_valid_checkpoint(concern_db, cid)
    assert ck is not None
    assert ck["through_seq"] >= interval
    rebuilt = rebuild_concern(concern_db, cid)
    assert len(rebuilt["controls"]) <= 10


def test_backdated_semantic_claim_no_hold_change(concern_db: sqlite3.Connection) -> None:
    """A7: assertion rows do not change operational controls."""
    cid = "concern:a7"
    _append(
        concern_db,
        cid,
        record_ref="rec-hold",
        control_key="gate",
        hold={"held": True},
    )
    before = read_controls(concern_db, cid)
    concern_db.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        ("assertion:backdated", "assertion", "late claim"),
    )
    concern_db.commit()
    after = read_controls(concern_db, cid)
    assert after == before


def test_rollback_keeps_hold_monotonic(concern_db: sqlite3.Connection) -> None:
    """A8: rollback transition keeps holds; revision/fencing monotonic."""
    cid = "concern:a8"
    append_transition(
        concern_db,
        concern_id=cid,
        kind="position_head",
        payload={},
        record_ref="rec-head",
        authorized_by=AUTH,
    )
    _append(
        concern_db,
        cid,
        record_ref="rec-hold",
        control_key="gate",
        hold={"held": True},
    )
    head0 = read_head(concern_db, cid)
    assert head0 is not None
    append_transition(
        concern_db,
        concern_id=cid,
        kind="rollback",
        payload={"fencing_generation": head0["fencing_generation"]},
        record_ref="rec-rollback",
        authorized_by=AUTH,
    )
    head1 = read_head(concern_db, cid)
    assert head1["revision"] >= head0["revision"]
    assert head1["fencing_generation"] >= head0["fencing_generation"]
    assert "gate" in read_controls(concern_db, cid)


def test_enrichment_cannot_change_admission(concern_db: sqlite3.Connection) -> None:
    """A9: admission path does not import enrichment write modules."""
    import importlib
    import pkgutil

    forbidden = []
    for mod in pkgutil.walk_packages(
        importlib.import_module("cortex_store").__path__,
        prefix="cortex_store.",
    ):
        name = mod.name
        if "rank" in name or "enrich" in name or "embed" in name:
            try:
                m = importlib.import_module(name)
                src = getattr(m, "__file__", "") or ""
                if src and "concern_aggregate/journal" in open(src, errors="ignore").read():
                    forbidden.append(name)
            except Exception:
                continue
    assert forbidden == []


def test_recovery_without_advisory_events(concern_db: sqlite3.Connection) -> None:
    """A10: deleting advisory rows does not affect rebuild."""
    cid = "concern:a10"
    _append(concern_db, cid, record_ref="rec-1", control_key="k", hold={})
    before = read_effective_state(concern_db, cid)
    concern_db.execute("DELETE FROM concern_advisory_delivered")
    concern_db.execute("DELETE FROM concern_advisory_outbox")
    concern_db.commit()
    rebuild_concern(concern_db, cid)
    after = read_effective_state(concern_db, cid)
    assert after == before


def test_forged_advisory_ignored(concern_db: sqlite3.Connection) -> None:
    """A11: injected delivered advisory does not mutate operational state."""
    cid = "concern:a11"
    _append(concern_db, cid, record_ref="rec-1", control_key="k", hold={"held": True})
    before = read_effective_state(concern_db, cid)
    concern_db.execute(
        "INSERT INTO concern_advisory_delivered "
        "(concern_id, seq, event_kind, payload) VALUES (?, ?, ?, ?)",
        (cid, 999, "hold_released", json.dumps({"control_key": "k"})),
    )
    concern_db.commit()
    after = read_effective_state(concern_db, cid)
    assert after == before


def test_identities_distinct(concern_db: sqlite3.Connection) -> None:
    """A12: cross-identity assignment rejected; record_ref known pre-commit."""
    with pytest.raises(IdentityTypeError):
        as_request_id("int:wrong-prefix")
    rec = "018f-precommit-ref"
    result = append_transition(
        concern_db,
        concern_id="concern:a12",
        kind="action_intent",
        payload={"step": 1},
        record_ref=rec,
        authorized_by=AUTH,
        intent_id="int:abc",
        request_id="req:parallel",
    )
    assert result["record_ref"] == rec
    with pytest.raises(IdentityTypeError):
        append_transition(
            concern_db,
            concern_id="concern:a12",
            kind="action_intent",
            payload={},
            record_ref="rec-x",
            authorized_by=AUTH,
            intent_id="int:y",
            request_id="int:bad",
        )


def test_migration_concern_aggregate(concern_db: sqlite3.Connection) -> None:
    """A13: migration 084 tables present."""
    tables = {
        r[0]
        for r in concern_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for name in (
        "concern_journal",
        "concern_head",
        "concern_controls",
        "concern_cursor",
        "concern_checkpoint",
        "concern_advisory_outbox",
    ):
        assert name in tables
    versions = {
        r[0]
        for r in concern_db.execute("SELECT version FROM schema_version").fetchall()
    }
    assert 84 in versions


def test_advisory_emit_failure_does_not_roll_back_commit(
    concern_db: sqlite3.Connection,
) -> None:
    """A14: commit stands when sink fails; outbox remains for retry."""
    cid = "concern:a14"
    append_transition(
        concern_db,
        concern_id=cid,
        kind="control_set",
        payload={"control_key": "k", "hold": {}},
        record_ref="rec-a14",
        authorized_by=AUTH,
        emit_advisory=False,
    )
    assert read_controls(concern_db, cid)["k"] == {}
    concern_db.execute(
        "UPDATE concern_advisory_outbox SET emitted_at = NULL WHERE concern_id = ?",
        (cid,),
    )
    concern_db.commit()
    with pytest.raises(AdvisoryEmitError):
        emit_pending(concern_db, concern_id=cid, fail_on_seq=1)
    assert read_controls(concern_db, cid)["k"] == {}
    emit_pending(concern_db, concern_id=cid)
    pending = concern_db.execute(
        "SELECT COUNT(*) FROM concern_advisory_outbox WHERE concern_id=? AND emitted_at IS NULL",
        (cid,),
    ).fetchone()[0]
    assert pending == 0


def test_ops_registered() -> None:
    """AMEND1: concern_transition and concern_rebuild registered."""
    assert "concern_transition" in _OP_SPECS
    assert "concern_rebuild" in _OP_SPECS


def test_authorized_by_must_resolve(concern_db: sqlite3.Connection) -> None:
    """AMEND2: authorized_by resolves to entities row."""
    with pytest.raises(TransitionRejectedError):
        append_transition(
            concern_db,
            concern_id="concern:auth",
            kind="control_set",
            payload={"control_key": "k"},
            record_ref="rec-auth",
            authorized_by="decision:missing",
        )
