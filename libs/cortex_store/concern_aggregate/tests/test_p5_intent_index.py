"""Phase-5 read-only intent index (A1–A8, R1–R12)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cortex_store._test_db_bootstrap import fresh_migrated_connection
from cortex_store.concern_aggregate import effects as effects_mod
from cortex_store.concern_aggregate import intent_index as idx
from cortex_store.concern_aggregate.canonical_json import CanonicalJsonError
from cortex_store.concern_aggregate.effects import (
    STATE_ADMITTED,
    STATE_FAILED,
    STATE_OUTCOME_UNKNOWN,
    STATE_RECONCILE_REQUIRED_RETRY,
    STATE_SUCCEEDED,
    LeaseHeldError,
    acquire_lease,
    admit_effect,
    compute_effect_key,
    reconcile_effect,
    record_outcome,
    record_recovery_decision,
    resolve_action_intent,
    set_hold,
)
from cortex_store.concern_aggregate.intent_index import read_intent_index
from cortex_store.concern_aggregate.journal import append_transition
from cortex_store.concern_aggregate.recovery import rebuild_concern
from cortex_store.concern_aggregate.tests.conftest import seed_authority
from cortex_store.concern_aggregate.tests.test_p3_effects import _PROV, _ev
from execution_conformance.adapters import HealthOnlyAdapter
from execution_conformance.states import ExecutionState

pytestmark = pytest.mark.offline

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
AUTH = "decision:test-auth"


class StubOwner:
    def __init__(
        self,
        *,
        healthy: bool = True,
        health_raises: bool = False,
        lookups: dict[str, Any] | None = None,
        raise_keys: set[str] | None = None,
    ) -> None:
        self.healthy = healthy
        self.health_raises = health_raises
        self.lookups = lookups or {}
        self.raise_keys = raise_keys or set()
        self.calls: list[str] = []

    def health(self) -> bool:
        self.calls.append("health")
        if self.health_raises:
            raise RuntimeError("health down")
        return self.healthy

    def lookup(self, identity_key: str) -> Any:
        self.calls.append(identity_key)
        if identity_key in self.raise_keys:
            raise RuntimeError("lookup down")
        if identity_key not in self.lookups:
            raise AssertionError(identity_key)
        return self.lookups[identity_key]


class _Found:
    def __init__(
        self,
        *,
        found: bool = True,
        state: str | None = "running",
        epoch_status: str = "ok",
        store_epoch: str = "ep1",
    ) -> None:
        self.found = found
        self.state = state
        self.epoch_status = epoch_status
        self.store_epoch = store_epoch
        self.unknown_reason = None
        self.actuator_ref = None


def _admit(
    conn: sqlite3.Connection, cid: str, work: str, slot: str = "slot"
) -> tuple[str, str, int]:
    gen = acquire_lease(
        conn,
        concern_id=cid,
        work_key=work,
        holder="holder-a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        conn,
        concern_id=cid,
        work_key=work,
        action_slot=slot,
        run_generation=1,
        now=T0,
    )
    result = admit_effect(
        conn,
        intent_id=iid,
        effect_kind="dispatch",
        request={"op": work},
        request_id=f"req:{work}",
        fencing_token=gen,
        now=T0,
    )
    return iid, result.effect_key, gen


def _keys(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        found.extend(obj.keys())
        for value in obj.values():
            found.extend(_keys(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_keys(value))
    return found


def test_a1_unavailable_empty_and_not_found(concern_db: sqlite3.Connection) -> None:
    """A1/R1/R12: missing owner, health, lookup, not_found, and empty ledger."""
    cid = "concern:a1"
    _iid, ek, _gen = _admit(concern_db, cid, "wk:a1")
    idx_none = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert idx_none.derived["observations"][0]["state"] == "unavailable"
    assert idx_none.blocked is True

    down = StubOwner(healthy=False)
    idx_health = read_intent_index(concern_db, cid, now=T0, owner=down)
    assert idx_health.derived["observations"][0]["state"] == "unavailable"
    assert down.calls == ["health"]

    boom = StubOwner(raise_keys={ek})
    idx_raise = read_intent_index(concern_db, cid, now=T0, owner=boom)
    assert idx_raise.derived["observations"][0]["state"] == "unavailable"

    missing = StubOwner(lookups={ek: _Found(found=False)})
    idx_nf = read_intent_index(concern_db, cid, now=T0, owner=missing)
    assert idx_nf.derived["observations"][0]["state"] == "not_found"
    assert any(r["cause"] == "not_found" for r in idx_nf.blocked_reasons)

    epoch = StubOwner(lookups={ek: _Found(found=False, epoch_status="unavailable")})
    idx_epoch = read_intent_index(concern_db, cid, now=T0, owner=epoch)
    assert idx_epoch.derived["observations"][0]["state"] == "unavailable"

    empty_owner = StubOwner()
    empty = read_intent_index(concern_db, "concern:empty", now=T0, owner=empty_owner)
    assert empty.completeness == "complete"
    assert empty.blocked is False
    assert empty.intents == ()
    assert empty.owner_consulted is False
    assert empty_owner.calls == []
    assert empty.completeness_scope == "ledger"


def test_a1c_lookup_raises_for_one_effect(concern_db: sqlite3.Connection) -> None:
    """A1(c): one lookup failure leaves the other effect observed."""
    cid = "concern:a1c"
    _i1, ek1, _g = _admit(concern_db, cid, "wk:one", slot="s1")
    _i2, ek2, _g2 = _admit(concern_db, cid, "wk:two", slot="s2")
    owner = StubOwner(lookups={ek2: _Found(state="running")}, raise_keys={ek1})
    got = read_intent_index(concern_db, cid, now=T0, owner=owner)
    by_key = {o["effect_key"]: o["state"] for o in got.derived["observations"]}
    assert by_key[ek1] == "unavailable"
    assert by_key[ek2] == "running"
    assert owner.calls.count("health") == 1


def test_a2_integrity_blocks(concern_db: sqlite3.Connection) -> None:
    """A2/R6: orphan, mismatch, unknown state, and concern mismatch."""
    cid = "concern:a2"
    iid, _ek, gen = _admit(concern_db, cid, "wk:a2")
    orphan = "e" * 64
    concern_db.execute(
        "INSERT INTO concern_effects (effect_key, concern_id, work_key, intent_id, "
        "effect_kind, material_fingerprint, fencing_token, state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            orphan,
            cid,
            "wk:a2",
            "int:" + "ab" * 16,
            "dispatch",
            "fp",
            gen,
            STATE_ADMITTED,
            "t",
            "t",
        ),
    )
    concern_db.commit()
    got = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(
        e["kind"] == "orphan_effect" and e["effect_key"] == orphan
        for e in got.integrity_errors
    )
    assert got.completeness == "incomplete"
    assert got.blocked is True
    assert any(r["effect_key"] == orphan for r in got.blocked_reasons)

    concern_db.execute(
        "UPDATE concern_effects SET work_key = ? WHERE intent_id = ?",
        ("wk:other", iid),
    )
    concern_db.commit()
    mismatch = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(e["kind"] == "work_key_mismatch" for e in mismatch.integrity_errors)

    concern_db.execute(
        "UPDATE concern_effects SET state = ? WHERE intent_id = ?",
        ("not-a-state", iid),
    )
    concern_db.commit()
    unknown = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(e["kind"] == "unknown_state" for e in unknown.integrity_errors)

    other = "concern:other"
    iid_b, ek_b, gen_b = _admit(concern_db, other, "wk:b")
    concern_db.execute(
        "UPDATE concern_effects SET concern_id = ? WHERE effect_key = ?",
        (other, ek_b),
    )
    # Point the foreign effect at this concern's intent while keeping the other concern id.
    concern_db.execute(
        "UPDATE concern_effects SET intent_id = ?, concern_id = ? WHERE effect_key = ?",
        (iid, "concern:foreign", ek_b),
    )
    del gen_b, iid_b
    concern_db.commit()
    foreign = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(
        e["kind"] == "concern_mismatch" and e["effect_key"] == ek_b
        for e in foreign.integrity_errors
    )


def test_a3_same_boundary_and_negative_controls(concern_db: sqlite3.Connection) -> None:
    """A3/R3/R12: digest ignores owner and now; it moves when the ledger moves."""
    cid = "concern:a3"
    iid, ek, gen = _admit(concern_db, cid, "wk:a3")
    path = concern_db.execute("PRAGMA database_list").fetchone()[2]
    other = sqlite3.connect(path)
    owner_a = StubOwner(lookups={ek: _Found(state="running")})
    owner_b = StubOwner(lookups={ek: _Found(state="completed")})
    early = T0
    late = T0 + timedelta(hours=2)
    first = read_intent_index(
        concern_db, cid, now=early, owner=owner_a, owner_label="alpha"
    )
    second = read_intent_index(other, cid, now=late, owner=owner_b, owner_label="beta")
    assert first.cortex_digest == second.cortex_digest
    assert first.cortex_half == second.cortex_half
    assert first.derived["observations"][0]["source"] == "alpha"
    assert second.derived["observations"][0]["source"] == "beta"
    assert (
        first.derived["observations"][0]["as_of"]
        != second.derived["observations"][0]["as_of"]
    )
    assert first.derived["leases"][0]["lease_live"] is True
    assert second.derived["leases"][0]["lease_live"] is False

    before = first.cortex_digest
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"ok": True},
        fencing_token=gen,
        now=T0,
    )
    after_write = read_intent_index(
        concern_db, cid, now=early, owner=None
    ).cortex_digest
    assert after_write != before

    acquire_lease(
        concern_db,
        concern_id=cid,
        work_key="wk:a3",
        holder="holder-a",
        lease_expires_at=T0 + timedelta(hours=3),
        now=T0,
    )
    after_lease = read_intent_index(
        concern_db, cid, now=early, owner=None
    ).cortex_digest
    assert after_lease != after_write

    seed_authority(concern_db)
    set_hold(
        concern_db,
        concern_id=cid,
        control_key="drain",
        hold={"on": True},
        authorized_by=AUTH,
        record_ref="rec-hold-a3",
        now=T0,
    )
    held = read_intent_index(concern_db, cid, now=early, owner=None)
    after_hold = held.cortex_digest
    assert after_hold != after_lease
    assert held.cortex_half["holds"] == ["drain"]

    append_transition(
        concern_db,
        concern_id=cid,
        kind="action_intent",
        payload={"note": "journal", "intent_id": "int:" + "cd" * 16},
        record_ref="rec-journal-a3",
        authorized_by=AUTH,
        intent_id="int:" + "cd" * 16,
    )
    after_journal = read_intent_index(concern_db, cid, now=early, owner=None)
    assert after_journal.cortex_digest != after_hold
    assert "int:" + "cd" * 16 in after_journal.journal_intents
    assert iid not in after_journal.journal_intents
    other.close()


def test_a4_r8_conflict(concern_db: sqlite3.Connection) -> None:
    """A4/R8: terminal disagreement, including running; agreement is not conflict."""
    cid = "concern:a4"
    _iid, ek, gen = _admit(concern_db, cid, "wk:a4")
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"ok": 1},
        fencing_token=gen,
        now=T0,
    )
    failed_owner = StubOwner(lookups={ek: _Found(state="failed")})
    got = read_intent_index(concern_db, cid, now=T0, owner=failed_owner)
    assert got.derived["observations"][0]["state"] == "conflict"

    running = StubOwner(lookups={ek: _Found(state="running")})
    assert (
        read_intent_index(concern_db, cid, now=T0, owner=running).derived[
            "observations"
        ][0]["state"]
        == "conflict"
    )

    done = StubOwner(lookups={ek: _Found(state="completed")})
    ok = read_intent_index(concern_db, cid, now=T0, owner=done)
    assert ok.derived["observations"][0]["state"] == "completed"

    cid2 = "concern:a4b"
    _i2, ek2, gen2 = _admit(concern_db, cid2, "wk:a4b")
    record_outcome(
        concern_db,
        effect_key=ek2,
        outcome=STATE_FAILED,
        receipt=None,
        fencing_token=gen2,
        now=T0 + timedelta(seconds=2),
        evidence=_ev("not_applied", ek2, T0 + timedelta(seconds=2), "req:wk:a4b"),
        evidence_provider=_PROV,
    )
    completed = StubOwner(lookups={ek2: _Found(state="completed")})
    bad = read_intent_index(concern_db, cid2, now=T0, owner=completed)
    assert bad.derived["observations"][0]["state"] == "conflict"
    assert any(r["cause"] == "failed_conflict" for r in bad.blocked_reasons)

    still = StubOwner(lookups={ek2: _Found(state="running")})
    assert (
        read_intent_index(concern_db, cid2, now=T0, owner=still).derived[
            "observations"
        ][0]["state"]
        == "conflict"
    )
    cancelled = StubOwner(lookups={ek2: _Found(state="cancelled")})
    assert (
        read_intent_index(concern_db, cid2, now=T0, owner=cancelled).derived[
            "observations"
        ][0]["state"]
        == "cancelled"
    )


def test_a5_phase3_states_claim_and_receipt(concern_db: sqlite3.Connection) -> None:
    """A5/R7/R11: unknown, reconcile, expired receipt, boundary lease, stale token."""
    cid = "concern:a5"
    _iid, ek, gen = _admit(concern_db, cid, "wk:a5")
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=gen,
        now=T0,
    )
    unknown = read_intent_index(
        concern_db, cid, now=T0, owner=StubOwner(lookups={ek: _Found(state="running")})
    )
    assert unknown.blocked is True
    assert any(r["cause"] == STATE_OUTCOME_UNKNOWN for r in unknown.blocked_reasons)

    reconcile_effect(
        concern_db,
        effect_key=ek,
        observed_applied=False,
        now=T0 + timedelta(seconds=2),
        evidence=_ev("not_applied", ek, T0 + timedelta(seconds=2), "req:wk:a5"),
        evidence_provider=_PROV,
    )
    recon = read_intent_index(
        concern_db, cid, now=T0, owner=StubOwner(lookups={ek: _Found(state="running")})
    )
    assert any(
        r["cause"] == STATE_RECONCILE_REQUIRED_RETRY for r in recon.blocked_reasons
    )

    cid_s = "concern:a5s"
    _is, eks, gens = _admit(concern_db, cid_s, "wk:succ")
    record_outcome(
        concern_db,
        effect_key=eks,
        outcome=STATE_SUCCEEDED,
        receipt={"id": "r"},
        fencing_token=gens,
        now=T0,
    )
    exp = T0 + timedelta(hours=1)
    at_exp = read_intent_index(
        concern_db,
        cid_s,
        now=exp,
        owner=StubOwner(lookups={eks: _Found(state="completed")}),
    )
    assert at_exp.derived["leases"][0]["lease_live"] is True
    with pytest.raises(LeaseHeldError):
        acquire_lease(
            concern_db,
            concern_id=cid_s,
            work_key="wk:succ",
            holder="holder-b",
            lease_expires_at=exp + timedelta(hours=1),
            now=exp,
        )
    later = read_intent_index(
        concern_db,
        cid_s,
        now=exp + timedelta(seconds=1),
        owner=StubOwner(lookups={eks: _Found(state="completed")}),
    )
    eff = later.derived["effects"][0]
    assert eff["claim_status"] == "expired"
    assert eff["receipt"] == {"id": "r"}

    acquire_lease(
        concern_db,
        concern_id=cid_s,
        work_key="wk:succ",
        holder="holder-b",
        lease_expires_at=exp + timedelta(hours=2),
        now=exp + timedelta(seconds=1),
    )
    stale = read_intent_index(
        concern_db,
        cid_s,
        now=exp + timedelta(seconds=1),
        owner=StubOwner(lookups={eks: _Found(state="completed")}),
    )
    assert stale.derived["effects"][0]["claim_status"] == "stale"
    assert stale.derived["effects"][0]["state"] == STATE_SUCCEEDED


def test_a6_read_does_not_write_and_preserves_caller_txn(
    concern_db: sqlite3.Connection,
) -> None:
    """A6/R11: query_only read; pending caller transaction survives ValueError."""
    cid = "concern:a6"
    _admit(concern_db, cid, "wk:a6")
    before = concern_db.total_changes
    read_intent_index(concern_db, cid, now=T0, owner=None)
    assert concern_db.total_changes == before
    assert concern_db.in_transaction is False
    src = Path(idx.__file__).read_text(encoding="utf-8")
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert verb not in src

    concern_db.execute("BEGIN")
    concern_db.execute(
        "INSERT INTO concern_effect_leases (concern_id, work_key, holder, owner_generation, lease_expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("concern:pending", "wk:p", "h", 1, "2026-01-01T13:00:00+00:00"),
    )
    with pytest.raises(ValueError, match="transaction"):
        read_intent_index(concern_db, cid, now=T0, owner=None)
    row = concern_db.execute(
        "SELECT holder FROM concern_effect_leases WHERE concern_id = ?",
        ("concern:pending",),
    ).fetchone()
    assert row is not None and row[0] == "h"
    concern_db.commit()
    with pytest.raises(ValueError, match="timezone-aware"):
        read_intent_index(concern_db, cid, now=datetime(2026, 1, 1), owner=None)


def test_a7_journal_intents_separate(concern_db: sqlite3.Connection) -> None:
    """A7/R9: Phase-1 journal ids are counted apart from ledger intents."""
    cid = "concern:a7"
    seed_authority(concern_db)
    ledger_id, _ek, _g = _admit(concern_db, cid, "wk:a7")
    journal_id = "int:" + "ef" * 16
    append_transition(
        concern_db,
        concern_id=cid,
        kind="action_intent",
        payload={"k": 1, "intent_id": journal_id},
        record_ref="rec-a7",
        authorized_by=AUTH,
        intent_id=journal_id,
    )
    got = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert got.integrity_errors == ()
    assert got.completeness == "complete"
    assert got.journal_intents == (journal_id,)
    assert got.journal_intents_count == 1
    assert [row["intent_id"] for row in got.intents] == [ledger_id]
    assert journal_id != ledger_id


def test_a8_rebuild_keeps_digest_and_cursor(concern_db: sqlite3.Connection) -> None:
    """A8/R11: rebuild_concern leaves the ledger digest and cursor token."""
    cid = "concern:a8"
    seed_authority(concern_db)
    _admit(concern_db, cid, "wk:a8")
    append_transition(
        concern_db,
        concern_id=cid,
        kind="action_intent",
        payload={"k": 1, "intent_id": "int:" + "11" * 16},
        record_ref="rec-a8",
        authorized_by=AUTH,
        intent_id="int:" + "11" * 16,
    )
    set_hold(
        concern_db,
        concern_id=cid,
        control_key="cancel",
        hold={"on": True},
        authorized_by=AUTH,
        record_ref="rec-a8-hold",
        now=T0,
    )
    before = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert before.integrity_errors == ()
    assert before.completeness == "complete"
    assert before.journal_intents
    assert before.cortex_half["holds"]
    rebuild_concern(concern_db, cid)
    after = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert after.integrity_errors == ()
    assert after.cortex_digest == before.cortex_digest
    assert after.cursor_token == before.cursor_token


def test_r1_health_raise_is_unavailable(concern_db: sqlite3.Connection) -> None:
    """R1 step 1: health() raising marks every effect unavailable and skips lookup."""
    cid = "concern:health-raise"
    _admit(concern_db, cid, "wk:health-raise")
    owner = StubOwner(health_raises=True)
    concern_db.execute("PRAGMA query_only=ON")
    prior_query_only = int(concern_db.execute("PRAGMA query_only").fetchone()[0])
    prior_in_tx = concern_db.in_transaction
    got = read_intent_index(concern_db, cid, now=T0, owner=owner)
    observations = got.derived["observations"]
    assert observations
    assert all(obs["state"] == "unavailable" for obs in observations)
    assert all(
        str(obs["reason"]).startswith("health_error:RuntimeError")
        for obs in observations
    )
    assert got.owner_consulted is True
    assert got.blocked is True
    assert owner.calls == ["health"]
    assert (
        int(concern_db.execute("PRAGMA query_only").fetchone()[0]) == prior_query_only
    )
    assert concern_db.in_transaction is prior_in_tx
    concern_db.execute("PRAGMA query_only=OFF")


def test_identity_key_error_skips_lookup(concern_db: sqlite3.Connection) -> None:
    """identity_key_for raising is unknown and never reaches owner.lookup."""
    cid = "concern:id-key-error"
    _admit(concern_db, cid, "wk:id-key")
    owner = StubOwner(lookups={})
    got = read_intent_index(
        concern_db,
        cid,
        now=T0,
        owner=owner,
        identity_key_for=lambda _e: 1 / 0,
    )
    obs = got.derived["observations"]
    assert len(obs) == 1
    assert obs[0]["state"] == "unknown"
    assert str(obs[0]["reason"]).startswith("identity_key_error:ZeroDivisionError")
    assert owner.calls == ["health"]


def test_r2_health_only_and_retry_and_lease_without_intent(
    concern_db: sqlite3.Connection,
) -> None:
    """R2: HealthOnlyAdapter blocks an admitted effect; retry and stray lease block."""
    cid = "concern:r2"
    _iid, ek, gen = _admit(concern_db, cid, "wk:r2")
    got = read_intent_index(concern_db, cid, now=T0, owner=HealthOnlyAdapter())
    assert got.blocked is True
    assert got.owner_consulted is True
    assert got.derived["observations"][0]["state"] == "not_found"
    assert any(r["cause"] == "not_found" for r in got.blocked_reasons)
    assert got.derived["observations"][0]["identity_key"] == ek
    assert got.derived["observations"][0]["identity_key_basis"] == "effect_key_default"

    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_FAILED,
        receipt=None,
        fencing_token=gen,
        now=T0 + timedelta(seconds=2),
        evidence=_ev("not_applied", ek, T0 + timedelta(seconds=2), "req:wk:r2"),
        evidence_provider=_PROV,
    )
    record_recovery_decision(
        concern_db,
        effect_key=ek,
        decision="retry",
        decided_by=AUTH,
        reason="again",
        now=T0,
    )
    seed_authority(concern_db)
    unresolved = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(
        r["cause"] == "failed_retry_unresolved" for r in unresolved.blocked_reasons
    )

    concern_db.execute(
        "INSERT INTO concern_effect_leases (concern_id, work_key, holder, owner_generation, lease_expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (cid, "wk:stray", "h", 1, "2026-01-01T13:00:00+00:00"),
    )
    concern_db.commit()
    stray = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(
        e["kind"] == "lease_without_intent" and e["work_key"] == "wk:stray"
        for e in stray.integrity_errors
    )

    cid_stale = "concern:r2-stale"
    _admit(concern_db, cid_stale, "wk:stale")
    concern_db.execute(
        "UPDATE concern_effect_leases SET owner_generation = owner_generation + 1 "
        "WHERE concern_id = ?",
        (cid_stale,),
    )
    concern_db.commit()
    stale = read_intent_index(concern_db, cid_stale, now=T0, owner=None)
    assert stale.derived["effects"][0]["claim_status"] == "stale"
    assert any(
        r["cause"] == "admitted_stale_or_no_lease" for r in stale.blocked_reasons
    )


def test_r4_interleaved_writer_and_malformed(concern_db: sqlite3.Connection) -> None:
    """R4: a commit after the first read stays outside the snapshot; bad JSON is integrity."""
    cid = "concern:r4"
    _admit(concern_db, cid, "wk:r4")
    path = concern_db.execute("PRAGMA database_list").fetchone()[2]
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    concern_db.execute("PRAGMA journal_mode=WAL")

    def _inject(_label: str) -> None:
        idx.statement_boundary_probe = None
        writer.execute(
            "INSERT INTO concern_effect_leases (concern_id, work_key, holder, owner_generation, lease_expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, "wk:later", "h", 1, "2026-01-01T13:00:00+00:00"),
        )
        writer.commit()

    before = read_intent_index(concern_db, cid, now=T0, owner=None)
    idx.statement_boundary_probe = _inject
    try:
        during = read_intent_index(concern_db, cid, now=T0, owner=None)
    finally:
        idx.statement_boundary_probe = None
    assert during.cortex_half == before.cortex_half
    after = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(row["work_key"] == "wk:later" for row in after.cortex_half["leases"])
    writer.close()

    concern_db.execute(
        "UPDATE concern_effects SET receipt_json = ? WHERE concern_id = ?",
        ("{", cid),
    )
    concern_db.commit()
    bad = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(e["kind"] == "malformed_json" for e in bad.integrity_errors)


def test_r4_non_canonical_does_not_raise(
    concern_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: a JCS failure is integrity ``non_canonical`` and the read returns."""
    cid = "concern:r4b"
    _admit(concern_db, cid, "wk:r4b")

    def _boom(_value: Any) -> str:
        raise CanonicalJsonError("nope")

    monkeypatch.setattr(idx, "jcs_serialize", _boom)
    got = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert got.cortex_digest == ""
    assert any(e["kind"] == "non_canonical" for e in got.integrity_errors)


def test_r10_view_lag(concern_db: sqlite3.Connection) -> None:
    """R10: a journal row with no cursor advance is view_lag."""
    cid = "concern:lag"
    concern_db.execute(
        "INSERT INTO concern_journal (concern_id, seq, record_ref, kind, payload, authorized_by, committed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, 1, "rec-lag", "note", "{}", AUTH, "2026-01-01T12:00:00+00:00"),
    )
    concern_db.commit()
    got = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(e["kind"] == "view_lag" for e in got.integrity_errors)
    assert got.cortex_half["journal_through_seq"] == 1
    assert got.through_seq == 0


def test_r12_isolation_order_and_state_pins(concern_db: sqlite3.Connection) -> None:
    """R12: concern isolation, digest order, permit-key absence, state pin."""
    c1, c2 = "concern:iso1", "concern:iso2"
    _admit(concern_db, c1, "wk:1", slot="b")
    _admit(concern_db, c1, "wk:1", slot="a")
    _admit(concern_db, c2, "wk:2")
    one = read_intent_index(concern_db, c1, now=T0, owner=None)
    two = read_intent_index(concern_db, c2, now=T0, owner=None)
    assert {row["work_key"] for row in one.intents} == {"wk:1"}
    assert {row["work_key"] for row in two.intents} == {"wk:2"}
    assert [row["action_slot"] for row in one.intents] == ["a", "b"]
    keys = _keys(one.to_dict())
    assert "allowed" not in keys
    assert "permit" not in keys
    assert {v for k, v in vars(effects_mod).items() if k.startswith("STATE_")} == (
        idx._KNOWN_EFFECT_STATES
    )
    assert {v for k, v in vars(effects_mod).items() if k.startswith("INTENT_")} == (
        idx._KNOWN_INTENT_STATES
    )
    assert idx._KNOWN_EFFECT_STATES == {
        effects_mod.STATE_ADMITTED,
        effects_mod.STATE_SUCCEEDED,
        effects_mod.STATE_FAILED,
        effects_mod.STATE_OUTCOME_UNKNOWN,
        effects_mod.STATE_RECONCILE_REQUIRED_RETRY,
    }
    assert idx._KNOWN_INTENT_STATES == {
        effects_mod.INTENT_OPEN,
        effects_mod.INTENT_HELD,
    }

    ek = compute_effect_key(
        concern_id=c1,
        work_key="wk:1",
        intent_id=one.intents[0]["intent_id"],
        effect_kind="dispatch",
    )
    assert ek
    caller = read_intent_index(
        concern_db,
        c1,
        now=T0,
        owner=StubOwner(lookups={"custom": _Found(state="reserved")}),
        identity_key_for=lambda _eff: "custom",
    )
    assert caller.derived["observations"][0]["identity_key_basis"] == "caller_supplied"
    assert caller.derived["observations"][0]["store_epoch"] == "ep1"


def _open_fresh(tmp_path: Path, template: Path, name: str) -> sqlite3.Connection:
    directory = tmp_path / name
    directory.mkdir()
    return fresh_migrated_connection(directory, template)


def _copy_concern_rows(
    src: sqlite3.Connection, dest: sqlite3.Connection, *, reverse: bool
) -> None:
    names = [
        str(row[0])
        for row in src.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'concern_%' "
            "ORDER BY name"
        )
    ]
    for name in names:
        cols = [str(row[1]) for row in src.execute(f"PRAGMA table_info({name})")]
        rows = src.execute(f"SELECT {', '.join(cols)} FROM {name}").fetchall()
        if reverse:
            rows = list(reversed(rows))
        if not rows:
            continue
        placeholders = ", ".join("?" for _ in cols)
        dest.executemany(
            f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({placeholders})",
            rows,
        )
    dest.commit()


def test_digest_independent_of_insert_order(
    tmp_path: Path, migrated_db_template: Path
) -> None:
    """R12: two fresh databases, same rows, opposite insert order, equal digests."""
    cid = "concern:order"
    z_first = _open_fresh(tmp_path, migrated_db_template, "z-first")
    for work, request_id, request in (
        ("wk:z", "req:z", {"n": 2}),
        ("wk:a", "req:a", {"n": 1}),
    ):
        gen = acquire_lease(
            z_first,
            concern_id=cid,
            work_key=work,
            holder="h",
            lease_expires_at=T0 + timedelta(hours=1),
            now=T0,
        )
        intent_id = resolve_action_intent(
            z_first,
            concern_id=cid,
            work_key=work,
            action_slot="m",
            run_generation=1,
            now=T0,
        )
        admit_effect(
            z_first,
            intent_id=intent_id,
            effect_kind="dispatch",
            request=request,
            request_id=request_id,
            fencing_token=gen,
            now=T0,
        )
    a_first = _open_fresh(tmp_path, migrated_db_template, "a-first")
    _copy_concern_rows(z_first, a_first, reverse=True)
    digest_z = read_intent_index(z_first, cid, now=T0, owner=None).cortex_digest
    digest_a = read_intent_index(a_first, cid, now=T0, owner=None).cortex_digest
    assert digest_z == digest_a
    assert digest_z
    assert [
        row["work_key"]
        for row in read_intent_index(a_first, cid, now=T0, owner=None).intents
    ] == [
        "wk:a",
        "wk:z",
    ]
    z_first.close()
    a_first.close()


def test_no_identity_key_and_fencing_ahead(concern_db: sqlite3.Connection) -> None:
    """D5/R6: missing identity key is unknown; token ahead of the lease is integrity."""
    cid = "concern:key"
    _iid, ek, gen = _admit(concern_db, cid, "wk:key")
    none_key = read_intent_index(
        concern_db,
        cid,
        now=T0,
        owner=StubOwner(),
        identity_key_for=lambda _eff: None,
    )
    assert none_key.derived["observations"][0]["state"] == "unknown"
    assert none_key.derived["observations"][0]["reason"] == "no_identity_key"
    concern_db.execute(
        "UPDATE concern_effects SET fencing_token = ? WHERE effect_key = ?",
        (gen + 3, ek),
    )
    concern_db.commit()
    ahead = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(
        e["kind"] == "fencing_token_ahead" and e["effect_key"] == ek
        for e in ahead.integrity_errors
    )


def test_unparseable_lease_and_admitted_no_lease(
    concern_db: sqlite3.Connection,
) -> None:
    """R6/R2: bad expiry and an admitted effect with no lease are integrity errors."""
    cid = "concern:lease"
    _iid, ek, _gen = _admit(concern_db, cid, "wk:lease")
    concern_db.execute("DELETE FROM concern_effect_leases WHERE concern_id = ?", (cid,))
    concern_db.commit()
    missing = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(
        e["kind"] == "admitted_stale_or_no_lease" and e["effect_key"] == ek
        for e in missing.integrity_errors
    )

    cid2 = "concern:badexp"
    _admit(concern_db, cid2, "wk:bad")
    concern_db.execute(
        "UPDATE concern_effect_leases SET lease_expires_at = ? WHERE concern_id = ?",
        ("not-a-date", cid2),
    )
    concern_db.commit()
    bad = read_intent_index(concern_db, cid2, now=T0, owner=None)
    assert any(e["kind"] == "unparseable_lease_expiry" for e in bad.integrity_errors)


def test_query_only_restored(concern_db: sqlite3.Connection) -> None:
    """R11: prior query_only 0 and 1 are restored after the read."""
    cid = "concern:qo"
    _admit(concern_db, cid, "wk:qo")
    concern_db.execute("PRAGMA query_only=OFF")
    read_intent_index(concern_db, cid, now=T0, owner=None)
    assert int(concern_db.execute("PRAGMA query_only").fetchone()[0]) == 0
    concern_db.execute("PRAGMA query_only=ON")
    read_intent_index(concern_db, cid, now=T0, owner=None)
    assert int(concern_db.execute("PRAGMA query_only").fetchone()[0]) == 1
    concern_db.execute("PRAGMA query_only=OFF")


def test_query_only_probe_raise_and_readonly(concern_db: sqlite3.Connection) -> None:
    """R11: a mid-snapshot raise restores query_only; a reader INSERT is readonly."""
    cid = "concern:probe"
    _admit(concern_db, cid, "wk:probe")
    concern_db.execute("PRAGMA query_only=OFF")

    def _boom(_label: str) -> None:
        raise RuntimeError("snapshot broke")

    idx.statement_boundary_probe = _boom
    try:
        with pytest.raises(RuntimeError, match="snapshot broke"):
            read_intent_index(concern_db, cid, now=T0, owner=None)
    finally:
        idx.statement_boundary_probe = None
    assert int(concern_db.execute("PRAGMA query_only").fetchone()[0]) == 0
    assert concern_db.in_transaction is False

    def _insert(_label: str) -> None:
        concern_db.execute(
            "INSERT INTO concern_effect_leases "
            "(concern_id, work_key, holder, owner_generation, lease_expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, "wk:mid", "h", 1, "2026-01-01T13:00:00+00:00"),
        )

    idx.statement_boundary_probe = _insert
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read_intent_index(concern_db, cid, now=T0, owner=None)
    finally:
        idx.statement_boundary_probe = None
    assert int(concern_db.execute("PRAGMA query_only").fetchone()[0]) == 0
    assert concern_db.in_transaction is False


def test_owner_write_after_query_only_restored(concern_db: sqlite3.Connection) -> None:
    """Owner lookup may write on the reader connection once the snapshot ends."""
    cid = "concern:owner-write"
    _iid, ek, _gen = _admit(concern_db, cid, "wk:ow")

    class _Writing:
        def health(self) -> bool:
            return True

        def lookup(self, identity_key: str) -> _Found:
            concern_db.execute(
                "UPDATE concern_effect_leases SET holder = ? WHERE concern_id = ?",
                ("writer", cid),
            )
            return _Found(state=ExecutionState.running)

    got = read_intent_index(concern_db, cid, now=T0, owner=_Writing())
    assert got.derived["observations"][0]["effect_key"] == ek
    assert got.derived["observations"][0]["state"] == "running"
    assert type(got.derived["observations"][0]["state"]) is str
    holder = concern_db.execute(
        "SELECT holder FROM concern_effect_leases WHERE concern_id = ?",
        (cid,),
    ).fetchone()
    assert holder is not None and holder[0] == "writer"


@pytest.mark.parametrize("epoch_status", ["unprovisioned", "", "weird"])
def test_epoch_not_ok_is_unknown_even_when_missing(
    concern_db: sqlite3.Connection, epoch_status: str
) -> None:
    """R1: a non-ok epoch is unknown, including when the execution row is absent."""
    cid = "concern:epoch"
    _iid, ek, _gen = _admit(concern_db, cid, "wk:epoch")
    owner = StubOwner(lookups={ek: _Found(found=False, epoch_status=epoch_status)})
    got = read_intent_index(concern_db, cid, now=T0, owner=owner)
    assert got.derived["observations"][0]["state"] == "unknown"
    assert got.derived["observations"][0]["state"] != "not_found"


@pytest.mark.parametrize(
    "state",
    [None, ExecutionState.outcome_unknown, ExecutionState.blocked_unknown],
)
def test_owner_unknown_states(
    concern_db: sqlite3.Connection, state: ExecutionState | None
) -> None:
    """R1: None, outcome_unknown, and blocked_unknown are unknown observations."""
    cid = "concern:owner-state"
    _iid, ek, _gen = _admit(concern_db, cid, "wk:os")
    owner = StubOwner(lookups={ek: _Found(state=state)})
    got = read_intent_index(concern_db, cid, now=T0, owner=owner)
    assert got.derived["observations"][0]["state"] == "unknown"


def test_consumed_retry_is_not_unresolved(concern_db: sqlite3.Connection) -> None:
    """A consumed retry does not block; an unconsumed retry does."""
    cid = "concern:consumed"
    _iid, ek, gen = _admit(concern_db, cid, "wk:consumed")
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_FAILED,
        receipt=None,
        fencing_token=gen,
        now=T0 + timedelta(seconds=2),
        evidence=_ev("not_applied", ek, T0 + timedelta(seconds=2), "req:wk:consumed"),
        evidence_provider=_PROV,
    )
    record_recovery_decision(
        concern_db,
        effect_key=ek,
        decision="retry",
        decided_by=AUTH,
        reason="again",
        now=T0,
    )
    open_retry = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert any(
        r["cause"] == "failed_retry_unresolved" for r in open_retry.blocked_reasons
    )
    raw = concern_db.execute(
        "SELECT recovery_json FROM concern_effects WHERE effect_key = ?",
        (ek,),
    ).fetchone()
    assert raw is not None
    recovery = json.loads(raw[0])
    for item in recovery:
        if item.get("decision") == "retry":
            item["consumed_attempt"] = True
    concern_db.execute(
        "UPDATE concern_effects SET recovery_json = ? WHERE effect_key = ?",
        (json.dumps(recovery), ek),
    )
    concern_db.commit()
    consumed = read_intent_index(concern_db, cid, now=T0, owner=None)
    assert not any(
        r["cause"] == "failed_retry_unresolved" for r in consumed.blocked_reasons
    )
