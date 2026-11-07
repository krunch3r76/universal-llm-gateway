"""Phase 3b.1 clock, evidence, and guarded-transition fixtures."""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from cortex_store.concern_aggregate import effects as effects_mod
from cortex_store.concern_aggregate.clock import FixedClock, SystemClock
from cortex_store.concern_aggregate.effect_gate import (
    EffectNotApplied,
    EffectOutcomeUnknownError,
    EffectPermit,
    run_effect,
)
from cortex_store.concern_aggregate.effects import (
    ALLOWED_TRANSITIONS,
    STATE_ADMITTED,
    STATE_FAILED,
    STATE_OUTCOME_UNKNOWN,
    STATE_RECONCILE_REQUIRED_RETRY,
    STATE_SUCCEEDED,
    EffectConflictError,
    EvidenceRejectedError,
    HoldActiveError,
    InvalidTransitionError,
    ReconcileRequiredError,
    RetryBudgetExceededError,
    _iso_utc,
    _set_state,
    acquire_lease,
    admit_effect,
    clear_hold,
    reconcile_effect,
    record_outcome,
    record_recovery_decision,
    resolve_action_intent,
    set_hold,
)
from cortex_store.concern_aggregate.evidence import EffectEvidence
from cortex_store.concern_aggregate.fault_injection import (
    set_between_resolve_and_admit,
)
from cortex_store.concern_aggregate.tests.conftest import seed_authority
from cortex_store.concern_aggregate.tests.test_p3_effects import (
    _PROV,
    AUTH,
    T0,
    _connect_wal,
    _ev,
)

pytestmark = pytest.mark.offline

_NOW_FUNCS = {
    "resolve_action_intent",
    "acquire_lease",
    "renew_lease",
    "admit_effect",
    "record_outcome",
    "reconcile_effect",
    "record_recovery_decision",
    "set_hold",
    "clear_hold",
}
_LITERAL = {
    ("admitted", "admitted", "auto_retry"),
    ("failed", "admitted", "decision"),
    ("reconcile_required_retry", "admitted", "decision"),
    ("admitted", "succeeded", "outcome"),
    ("admitted", "failed", "outcome"),
    ("admitted", "outcome_unknown", "outcome"),
    ("admitted", "outcome_unknown", "late_report"),
    ("admitted", "outcome_unknown", "sweep"),
    ("outcome_unknown", "succeeded", "reconcile"),
    ("outcome_unknown", "reconcile_required_retry", "reconcile"),
}
_STATES = [
    "admitted",
    "succeeded",
    "failed",
    "outcome_unknown",
    "reconcile_required_retry",
]
_VIAS = [
    "resend",
    "decision",
    "auto_retry",
    "outcome",
    "late_report",
    "reconcile",
    "sweep",
]
_PKG = Path(__file__).resolve().parents[1]


class _Stub:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls = 0
        self.name = "stub"

    def verify(self, evidence: EffectEvidence) -> bool:
        self.calls += 1
        return self.ok


def _counts(conn: sqlite3.Connection) -> tuple[int, int, int, int]:
    return tuple(
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "concern_effects",
            "concern_action_intents",
            "concern_effect_leases",
            "concern_journal",
        )
    )


def _setup(conn: sqlite3.Connection, cid: str = "concern:b") -> tuple[int, str]:
    seed_authority(conn)
    gen = acquire_lease(
        conn,
        concern_id=cid,
        work_key="wk",
        holder="h",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    intent = resolve_action_intent(
        conn, concern_id=cid, work_key="wk", action_slot="s", run_generation=1, now=T0
    )
    return gen, intent


@pytest.mark.parametrize("fname", sorted(_NOW_FUNCS))
def test_b1_naive_now_rejected(
    concern_db: sqlite3.Connection,
    fname: str,
    tmp_path: Path,
    migrated_db_template: Path,
) -> None:
    """Naive now (and lease expiry) raises ValueError before BEGIN, even if the writer is locked."""
    found = {
        name
        for name, obj in vars(effects_mod).items()
        if inspect.isfunction(obj)
        and not name.startswith("_")
        and "now" in inspect.signature(obj).parameters
    }
    assert found == _NOW_FUNCS
    naive = datetime(2026, 1, 1, 12, 0, 0)
    from cortex_store._test_db_bootstrap import fresh_migrated_connection

    locked = fresh_migrated_connection(tmp_path, migrated_db_template)
    seed_authority(locked)
    before = _counts(locked)
    path = next(r[2] for r in locked.execute("PRAGMA database_list") if r[1] == "main")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    victim = sqlite3.connect(path, isolation_level=None)
    victim.execute("PRAGMA busy_timeout=0")
    fn = getattr(effects_mod, fname)
    kwargs = {
        "conn": victim,
        "now": naive,
        "concern_id": "concern:b1",
        "work_key": "wk",
        "action_slot": "s",
        "run_generation": 1,
        "holder": "h",
        "lease_expires_at": T0 + timedelta(hours=1),
        "fencing_token": 1,
        "intent_id": "int:" + "ab" * 16,
        "effect_kind": "e",
        "request": {},
        "request_id": "req:b1",
        "effect_key": "k",
        "outcome": "succeeded",
        "receipt": None,
        "observed_applied": True,
        "decision": "retry",
        "decided_by": AUTH,
        "reason": "r",
        "control_key": "cancel",
        "hold": {},
        "authorized_by": AUTH,
        "record_ref": "rec-b1",
    }
    accepted = {p.name for p in inspect.signature(fn).parameters.values()}
    variants = [kwargs]
    if fname in ("acquire_lease", "renew_lease"):
        naive_expiry = dict(kwargs)
        naive_expiry["lease_expires_at"] = naive
        naive_expiry["now"] = T0
        variants = [kwargs, naive_expiry]
    for variant in variants:
        call = {k: v for k, v in variant.items() if k in accepted}
        with pytest.raises(ValueError):
            fn(**call)
    holder.rollback()
    assert _counts(locked) == before
    holder.close()
    victim.close()
    locked.close()


def test_b2_no_wall_clock_outside_clock_module() -> None:
    """Alias-resolving AST scan rejects wall-clock calls outside clock.py."""

    def flagged(source: str) -> bool:
        tree = ast.parse(source)
        datetime_names: set[str] = set()
        date_names: set[str] = set()
        time_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name == "datetime":
                        datetime_names.add(bound)
                    elif alias.name == "time":
                        return True
            if isinstance(node, ast.ImportFrom) and node.module in {"datetime", "time"}:
                if node.module == "time":
                    return True
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name == "datetime":
                        datetime_names.add(bound)
                    if alias.name == "date":
                        date_names.add(bound)
        banned = {
            "now",
            "utcnow",
            "today",
            "fromtimestamp",
            "time",
            "time_ns",
            "monotonic",
            "perf_counter",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr not in banned:
                    continue
                base = node.func.value
                if isinstance(base, ast.Name) and (
                    base.id in datetime_names
                    or base.id in date_names
                    or base.id in time_modules
                ):
                    return True
                if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                    if base.value.id in datetime_names and base.attr in {
                        "datetime",
                        "date",
                    }:
                        return True
        return False

    assert flagged("from datetime import datetime as dt\ndt.now()\n")
    for path in (_PKG / "effects.py", _PKG / "evidence.py", _PKG / "effect_gate.py"):
        assert flagged(path.read_text()) is False
    assert SystemClock().now().tzinfo is not None


def test_b3_resend_unverified_evidence_refused_no_write(
    concern_db: sqlite3.Connection,
) -> None:
    gen, intent = _setup(concern_db)
    admit_effect(
        concern_db,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen,
        now=T0,
    )
    key = concern_db.execute("SELECT effect_key FROM concern_effects").fetchone()[0]
    snap = concern_db.execute("SELECT * FROM concern_effects").fetchone()
    intent_snap = concern_db.execute("SELECT * FROM concern_action_intents").fetchone()
    stub = _Stub(False)
    evidence = _ev("resource_dedup", key, T0)
    with pytest.raises(ValueError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:s",
            fencing_token=gen,
            now=T0,
            resend_basis="resource_dedup",
            resend_evidence="nope",
            evidence_provider=stub,  # type: ignore[arg-type]
        )
    assert stub.calls == 0
    with pytest.raises(ValueError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:s",
            fencing_token=gen,
            now=T0,
            resend_basis="resource_dedup",
        )
    assert stub.calls == 0
    with pytest.raises(EvidenceRejectedError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:s",
            fencing_token=gen,
            now=T0,
            resend_basis="resource_dedup",
            resend_evidence=evidence,
            evidence_provider=stub,
        )
    assert stub.calls == 1
    assert concern_db.execute("SELECT * FROM concern_effects").fetchone() == snap
    assert (
        concern_db.execute("SELECT * FROM concern_action_intents").fetchone()
        == intent_snap
    )


def test_b4_resend_evidence_binds_effect_key_and_time(
    concern_db: sqlite3.Connection,
) -> None:
    gen, intent = _setup(concern_db)
    first = admit_effect(
        concern_db,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen,
        now=T0.replace(microsecond=900000),
    )
    before = concern_db.execute("SELECT * FROM concern_effects").fetchone()
    with pytest.raises(EvidenceRejectedError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:w",
            fencing_token=gen,
            now=T0 + timedelta(seconds=2),
            resend_basis="resource_dedup",
            resend_evidence=_ev("resource_dedup", "other-key", T0),
            evidence_provider=_PROV,
        )
    with pytest.raises(EvidenceRejectedError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:late",
            fencing_token=gen,
            now=T0,
            resend_basis="resource_dedup",
            resend_evidence=_ev(
                "resource_dedup", first.effect_key, T0 + timedelta(seconds=5)
            ),
            evidence_provider=_PROV,
        )
    with pytest.raises(EvidenceRejectedError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:sub",
            fencing_token=gen,
            now=T0 + timedelta(seconds=2),
            resend_basis="confirmed_not_accepted",
            resend_evidence=_ev(
                "confirmed_not_accepted",
                first.effect_key,
                T0.replace(microsecond=500000),
                "req:1",
            ),
            evidence_provider=_PROV,
        )
    with pytest.raises(EvidenceRejectedError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:sid",
            fencing_token=gen,
            now=T0 + timedelta(seconds=2),
            resend_basis="confirmed_not_accepted",
            resend_evidence=_ev(
                "confirmed_not_accepted",
                first.effect_key,
                T0 + timedelta(seconds=1),
                "req:nope",
            ),
            evidence_provider=_PROV,
        )
    assert concern_db.execute("SELECT * FROM concern_effects").fetchone() == before
    good = _ev("resource_dedup", first.effect_key, T0 + timedelta(seconds=1))
    permit = admit_effect(
        concern_db,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:ok",
        fencing_token=gen,
        now=T0 + timedelta(seconds=1),
        resend_basis="resource_dedup",
        resend_evidence=good,
        evidence_provider=_PROV,
    )
    assert permit.execution_allowed is True
    attempts = concern_db.execute(
        "SELECT attempts_json FROM concern_effects"
    ).fetchone()[0]
    import json

    last = json.loads(attempts)[-1]
    assert last["resend_basis"] == "resource_dedup"
    assert last["resend_evidence"] == good.to_json()


def test_b5_reconcile_not_applied_requires_verified_evidence(
    concern_db: sqlite3.Connection,
) -> None:
    gen, intent = _setup(concern_db)
    admitted = admit_effect(
        concern_db,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen,
        now=T0,
    )
    record_outcome(
        concern_db,
        effect_key=admitted.effect_key,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=gen,
        now=T0,
    )
    later = T0 + timedelta(seconds=2)
    with pytest.raises(ValueError):
        reconcile_effect(
            concern_db,
            effect_key=admitted.effect_key,
            observed_applied=False,
            now=later,
        )
    with pytest.raises(ValueError):
        reconcile_effect(
            concern_db,
            effect_key=admitted.effect_key,
            observed_applied=True,
            now=later,
            evidence=_ev("not_applied", admitted.effect_key, later, "req:1"),
            evidence_provider=_PROV,
        )
    stub = _Stub(False)
    with pytest.raises(EvidenceRejectedError):
        reconcile_effect(
            concern_db,
            effect_key=admitted.effect_key,
            observed_applied=False,
            now=later,
            evidence=_ev("not_applied", admitted.effect_key, later, "req:1"),
            evidence_provider=stub,
        )
    with pytest.raises(EvidenceRejectedError):
        reconcile_effect(
            concern_db,
            effect_key=admitted.effect_key,
            observed_applied=False,
            now=later,
            evidence=_ev("not_applied", admitted.effect_key, T0, "req:1"),
            evidence_provider=_PROV,
        )
    with pytest.raises(EvidenceRejectedError):
        reconcile_effect(
            concern_db,
            effect_key=admitted.effect_key,
            observed_applied=False,
            now=later,
            evidence=_ev("not_applied", admitted.effect_key, later, "req:other"),
            evidence_provider=_PROV,
        )
    assert (
        concern_db.execute("SELECT state FROM concern_effects").fetchone()[0]
        == STATE_OUTCOME_UNKNOWN
    )
    stored = _ev("not_applied", admitted.effect_key, later, "req:1")
    reconcile_effect(
        concern_db,
        effect_key=admitted.effect_key,
        observed_applied=False,
        now=later,
        evidence=stored,
        evidence_provider=_PROV,
    )
    recovery = json.loads(
        concern_db.execute("SELECT recovery_json FROM concern_effects").fetchone()[0]
    )
    assert recovery[-1]["kind"] == "reconcile"
    assert recovery[-1]["evidence"] == stored.to_json()
    assert (
        concern_db.execute("SELECT state FROM concern_effects").fetchone()[0]
        == STATE_RECONCILE_REQUIRED_RETRY
    )
    record_recovery_decision(
        concern_db,
        effect_key=admitted.effect_key,
        decision="retry",
        decided_by=AUTH,
        reason="ok",
        now=later,
    )


def test_b6_holds_require_aware_now(concern_db: sqlite3.Connection) -> None:
    seed_authority(concern_db)
    cid = "concern:b6"
    with pytest.raises(TypeError):
        set_hold(
            concern_db,
            concern_id=cid,
            control_key="cancel",
            hold={},
            authorized_by=AUTH,
            record_ref="r1",
        )  # type: ignore[call-arg]
    before = concern_db.execute("SELECT COUNT(*) FROM concern_journal").fetchone()[0]
    with pytest.raises(ValueError):
        set_hold(
            concern_db,
            concern_id=cid,
            control_key="cancel",
            hold={},
            authorized_by=AUTH,
            record_ref="r2",
            now=datetime(2026, 1, 1),
        )
    assert (
        concern_db.execute("SELECT COUNT(*) FROM concern_journal").fetchone()[0]
        == before
    )
    gen = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key="wk",
        holder="h",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    intent = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key="wk",
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    set_hold(
        concern_db,
        concern_id=cid,
        control_key="cancel",
        hold={},
        authorized_by=AUTH,
        record_ref="r3",
        now=T0,
    )
    with pytest.raises(HoldActiveError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:hold",
            fencing_token=gen,
            now=T0,
        )
    with pytest.raises(TypeError):
        clear_hold(
            concern_db,
            concern_id=cid,
            control_key="cancel",
            authorized_by=AUTH,
            record_ref="r4",
        )  # type: ignore[call-arg]
    stamped = T0 + timedelta(seconds=9)
    clear_hold(
        concern_db,
        concern_id=cid,
        control_key="cancel",
        authorized_by=AUTH,
        record_ref="r5",
        now=stamped,
    )
    updated = concern_db.execute(
        "SELECT updated_at FROM concern_action_intents WHERE intent_id = ?", (intent,)
    ).fetchone()
    if updated is not None and updated[0] is not None:
        assert updated[0] == _iso_utc(stamped)


def test_b7_transition_matrix(concern_db: sqlite3.Connection) -> None:
    assert ALLOWED_TRANSITIONS == frozenset(_LITERAL)
    gen, intent = _setup(concern_db)
    admitted = admit_effect(
        concern_db,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen,
        now=T0,
    )
    for src in _STATES:
        for dst in _STATES:
            for via in _VIAS:
                concern_db.execute(
                    "UPDATE concern_effects SET state = ? WHERE effect_key = ?",
                    (src, admitted.effect_key),
                )
                triple = (src, dst, via)
                if triple in _LITERAL:
                    _set_state(
                        concern_db,
                        effect_key=admitted.effect_key,
                        from_state=src,
                        to_state=dst,
                        via=via,
                        now_s=_iso_utc(T0),
                    )
                    concern_db.commit()
                else:
                    with pytest.raises(InvalidTransitionError):
                        _set_state(
                            concern_db,
                            effect_key=admitted.effect_key,
                            from_state=src,
                            to_state=dst,
                            via=via,
                            now_s=_iso_utc(T0),
                        )
                    if concern_db.in_transaction:
                        concern_db.rollback()
    concern_db.execute(
        "UPDATE concern_effects SET state = ? WHERE effect_key = ?",
        (STATE_SUCCEEDED, admitted.effect_key),
    )
    with pytest.raises(InvalidTransitionError):
        _set_state(
            concern_db,
            effect_key=admitted.effect_key,
            from_state=STATE_ADMITTED,
            to_state=STATE_FAILED,
            via="outcome",
            now_s=_iso_utc(T0),
        )

    def _state_write_guarded(sql: str) -> None:
        compact = " ".join(sql.split())
        upper = compact.upper()
        if not upper.startswith("UPDATE CONCERN_EFFECTS"):
            return
        set_part, separator, where = upper.partition(" WHERE ")
        if "STATE =" not in set_part.split("VALUES", 1)[0]:
            return
        assert separator == " WHERE "
        assert " AND STATE = " in f" {where}"

    def _traced(fn) -> None:
        captured: list[str] = []
        concern_db.set_trace_callback(captured.append)
        try:
            fn()
        finally:
            concern_db.set_trace_callback(None)
        assert captured
        for sql in captured:
            _state_write_guarded(sql)

    g2, intent2 = _setup(concern_db, "concern:trace")
    first = admit_effect(
        concern_db,
        intent_id=intent2,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=g2,
        now=T0,
    )
    _traced(
        lambda: admit_effect(
            concern_db,
            intent_id=intent2,
            effect_kind="e",
            request={"a": 1},
            request_id="req:resend",
            fencing_token=g2,
            now=T0 + timedelta(seconds=2),
            resend_basis="resource_dedup",
            resend_evidence=_ev(
                "resource_dedup", first.effect_key, T0 + timedelta(seconds=1)
            ),
            evidence_provider=_PROV,
        )
    )
    _traced(
        lambda: record_outcome(
            concern_db,
            effect_key=first.effect_key,
            outcome=STATE_FAILED,
            receipt=None,
            fencing_token=g2,
            now=T0 + timedelta(seconds=4),
            evidence=_ev(
                "not_applied", first.effect_key, T0 + timedelta(seconds=4), "req:resend"
            ),
            evidence_provider=_PROV,
        )
    )
    record_recovery_decision(
        concern_db,
        effect_key=first.effect_key,
        decision="retry",
        decided_by=AUTH,
        reason="trace",
        now=T0 + timedelta(seconds=4),
    )
    _traced(
        lambda: admit_effect(
            concern_db,
            intent_id=intent2,
            effect_kind="e",
            request={"a": 1},
            request_id="req:decision",
            fencing_token=g2,
            now=T0 + timedelta(seconds=4),
        )
    )
    g3, intent3 = _setup(concern_db, "concern:trace-ok")
    ok = admit_effect(
        concern_db,
        intent_id=intent3,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=g3,
        now=T0,
    )
    _traced(
        lambda: record_outcome(
            concern_db,
            effect_key=ok.effect_key,
            outcome=STATE_SUCCEEDED,
            receipt={"ok": True},
            fencing_token=g3,
            now=T0,
        )
    )
    g4, intent4 = _setup(concern_db, "concern:trace-unk")
    unk = admit_effect(
        concern_db,
        intent_id=intent4,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=g4,
        now=T0,
    )
    _traced(
        lambda: record_outcome(
            concern_db,
            effect_key=unk.effect_key,
            outcome=STATE_OUTCOME_UNKNOWN,
            receipt=None,
            fencing_token=g4,
            now=T0,
        )
    )
    later = T0 + timedelta(hours=2)
    g_late, intent_late = _setup(concern_db, "concern:trace-late")
    late_row = admit_effect(
        concern_db,
        intent_id=intent_late,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=g_late,
        now=T0,
    )
    _traced(
        lambda: record_outcome(
            concern_db,
            effect_key=late_row.effect_key,
            outcome=STATE_FAILED,
            receipt=None,
            fencing_token=0,
            now=T0 + timedelta(seconds=2),
            evidence=_ev(
                "not_applied", late_row.effect_key, T0 + timedelta(seconds=2), "req:1"
            ),
            evidence_provider=_PROV,
        )
    )
    g6, intent6 = _setup(concern_db, "concern:trace-rec")
    rec = admit_effect(
        concern_db,
        intent_id=intent6,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=g6,
        now=T0,
    )
    record_outcome(
        concern_db,
        effect_key=rec.effect_key,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=g6,
        now=T0,
    )
    _traced(
        lambda: reconcile_effect(
            concern_db,
            effect_key=rec.effect_key,
            observed_applied=True,
            now=T0,
        )
    )
    g7, intent7 = _setup(concern_db, "concern:trace-na")
    na = admit_effect(
        concern_db,
        intent_id=intent7,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=g7,
        now=T0,
    )
    record_outcome(
        concern_db,
        effect_key=na.effect_key,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=g7,
        now=T0,
    )
    _traced(
        lambda: reconcile_effect(
            concern_db,
            effect_key=na.effect_key,
            observed_applied=False,
            now=T0 + timedelta(seconds=2),
            evidence=_ev(
                "not_applied", na.effect_key, T0 + timedelta(seconds=2), "req:1"
            ),
            evidence_provider=_PROV,
        )
    )
    _traced(
        lambda: acquire_lease(
            concern_db,
            concern_id="concern:trace",
            work_key="wk",
            holder="next",
            lease_expires_at=later + timedelta(hours=1),
            now=later,
        )
    )
    tree = ast.parse((_PKG / "effects.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "_set_state":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                sql = " ".join(child.value.split()).upper()
                if (
                    sql.startswith("UPDATE CONCERN_EFFECTS")
                    and "STATE =" in sql.split("WHERE", 1)[0]
                ):
                    raise AssertionError(
                        f"state write outside _set_state in {node.name}"
                    )


def test_b8_effect_gate_crash_after_apply_no_second_execution(
    concern_db: sqlite3.Connection,
) -> None:
    gen, intent = _setup(concern_db)
    clock = FixedClock(T0)
    counter = {"n": 0}

    def executor(permit: EffectPermit) -> dict:
        assert permit.effect_key
        counter["n"] += 1
        return {"ok": True}

    with patch(
        "cortex_store.concern_aggregate.effect_gate.record_outcome",
        side_effect=RuntimeError("crash"),
    ) as patched:
        with pytest.raises(RuntimeError, match="crash"):
            run_effect(
                concern_db,
                intent_id=intent,
                effect_kind="e",
                request={"a": 1},
                request_id="req:1",
                fencing_token=gen,
                executor=executor,
                clock=clock,
                evidence_provider=_PROV,
            )
        assert patched.called
    again = run_effect(
        concern_db,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:2",
        fencing_token=gen,
        executor=executor,
        clock=clock,
        evidence_provider=_PROV,
    )
    assert again.executed is False and again.admit.execution_allowed is False
    assert again.receipt is None and again.state == STATE_ADMITTED and counter["n"] == 1

    def boom(permit: EffectPermit) -> dict:
        counter["n"] += 1
        raise RuntimeError("exec")

    clock2 = FixedClock(T0)
    gen2, intent2 = _setup(concern_db, "concern:b8b")
    with pytest.raises(EffectOutcomeUnknownError):
        run_effect(
            concern_db,
            intent_id=intent2,
            effect_kind="e",
            request={"a": 1},
            request_id="req:1",
            fencing_token=gen2,
            executor=boom,
            clock=clock2,
            evidence_provider=_PROV,
        )
    assert (
        concern_db.execute(
            "SELECT state FROM concern_effects WHERE intent_id = ?", (intent2,)
        ).fetchone()[0]
        == STATE_OUTCOME_UNKNOWN
    )
    with pytest.raises(ReconcileRequiredError):
        run_effect(
            concern_db,
            intent_id=intent2,
            effect_kind="e",
            request={"a": 1},
            request_id="req:3",
            fencing_token=gen2,
            executor=boom,
            clock=clock2,
            evidence_provider=_PROV,
        )
    assert counter["n"] == 2  # first crash case plus this executor; this case ran once

    gen3, intent3 = _setup(concern_db, "concern:b8a")
    clock3 = FixedClock(T0)

    def steal(permit: EffectPermit) -> dict:
        clock3.advance(timedelta(hours=2))
        acquire_lease(
            concern_db,
            concern_id="concern:b8a",
            work_key="wk",
            holder="other",
            lease_expires_at=clock3.now() + timedelta(hours=1),
            now=clock3.now(),
        )
        return {"ok": True}

    with pytest.raises(EffectOutcomeUnknownError):
        run_effect(
            concern_db,
            intent_id=intent3,
            effect_kind="e",
            request={"a": 1},
            request_id="req:1",
            fencing_token=gen3,
            executor=steal,
            clock=clock3,
            evidence_provider=_PROV,
        )
    assert (
        concern_db.execute(
            "SELECT state FROM concern_effects WHERE intent_id = ?", (intent3,)
        ).fetchone()[0]
        == STATE_OUTCOME_UNKNOWN
    )

    gen4, intent4 = _setup(concern_db, "concern:b8c")

    def not_applied(permit: EffectPermit) -> dict:
        raise EffectNotApplied()

    with pytest.raises(EffectOutcomeUnknownError):
        run_effect(
            concern_db,
            intent_id=intent4,
            effect_kind="e",
            request={"a": 1},
            request_id="req:1",
            fencing_token=gen4,
            executor=not_applied,
            clock=FixedClock(T0),
            evidence_provider=_PROV,
        )
    assert (
        concern_db.execute(
            "SELECT state FROM concern_effects WHERE intent_id = ?", (intent4,)
        ).fetchone()[0]
        == STATE_OUTCOME_UNKNOWN
    )

    calls = {"n": 0}
    gen5, intent5 = _setup(concern_db, "concern:b8d")
    admit_effect(
        concern_db,
        intent_id=intent5,
        effect_kind="e",
        request={"a": 1},
        request_id="req:seed",
        fencing_token=gen5,
        now=T0,
    )
    with pytest.raises(EffectConflictError):
        run_effect(
            concern_db,
            intent_id=intent5,
            effect_kind="e",
            request={"a": 2},
            request_id="req:other",
            fencing_token=gen5,
            executor=lambda permit: calls.__setitem__("n", 1) or {"ok": True},
            clock=FixedClock(T0),
            evidence_provider=_PROV,
        )
    assert calls["n"] == 0

    held = {"n": 0}
    gen6, intent6 = _setup(concern_db, "concern:b8e")

    def once(permit: EffectPermit) -> dict:
        held["n"] += 1
        return {"ok": True}

    with patch(
        "cortex_store.concern_aggregate.effect_gate.record_outcome",
        side_effect=RuntimeError("crash"),
    ):
        with pytest.raises(RuntimeError):
            run_effect(
                concern_db,
                intent_id=intent6,
                effect_kind="e",
                request={"a": 1},
                request_id="req:1",
                fencing_token=gen6,
                executor=once,
                clock=FixedClock(T0),
                evidence_provider=_PROV,
            )
    later = T0 + timedelta(hours=3)
    acquire_lease(
        concern_db,
        concern_id="concern:b8e",
        work_key="wk",
        holder="h",
        lease_expires_at=later + timedelta(hours=1),
        now=later,
    )
    with pytest.raises(ReconcileRequiredError):
        run_effect(
            concern_db,
            intent_id=intent6,
            effect_kind="e",
            request={"a": 1},
            request_id="req:2",
            fencing_token=gen6 + 1,
            executor=once,
            clock=FixedClock(later),
            evidence_provider=_PROV,
        )
    assert held["n"] == 1

    gen_ok, intent_ok = _setup(concern_db, "concern:b8-ok")
    clock_ok = FixedClock(T0)

    def applied(permit: EffectPermit) -> dict:
        clock_ok.advance(timedelta(seconds=2))
        raise EffectNotApplied(
            _ev(
                "not_applied",
                permit.effect_key,
                clock_ok.now(),
                permit.permit_request_id,
            )
        )

    result = run_effect(
        concern_db,
        intent_id=intent_ok,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen_ok,
        executor=applied,
        clock=clock_ok,
        evidence_provider=_PROV,
    )
    assert result.executed is True and result.state == STATE_FAILED

    gen_bad, intent_bad = _setup(concern_db, "concern:b8-bad")

    def rejected(permit: EffectPermit) -> dict:
        raise EffectNotApplied(
            _ev("not_applied", permit.effect_key, T0 + timedelta(seconds=2), "req:nope")
        )

    with pytest.raises(EffectOutcomeUnknownError):
        run_effect(
            concern_db,
            intent_id=intent_bad,
            effect_kind="e",
            request={"a": 1},
            request_id="req:1",
            fencing_token=gen_bad,
            executor=rejected,
            clock=FixedClock(T0 + timedelta(seconds=2)),
            evidence_provider=_PROV,
        )
    assert (
        concern_db.execute(
            "SELECT state FROM concern_effects WHERE intent_id = ?", (intent_bad,)
        ).fetchone()[0]
        == STATE_OUTCOME_UNKNOWN
    )

    gen_take, intent_take = _setup(concern_db, "concern:b8-take")
    clock_take = FixedClock(T0)

    def taken(permit: EffectPermit) -> dict:
        clock_take.advance(timedelta(hours=2))
        acquire_lease(
            concern_db,
            concern_id="concern:b8-take",
            work_key="wk",
            holder="other",
            lease_expires_at=clock_take.now() + timedelta(hours=1),
            now=clock_take.now(),
        )
        raise EffectNotApplied(
            _ev(
                "not_applied",
                permit.effect_key,
                clock_take.now(),
                permit.permit_request_id,
            )
        )

    with pytest.raises(EffectOutcomeUnknownError):
        run_effect(
            concern_db,
            intent_id=intent_take,
            effect_kind="e",
            request={"a": 1},
            request_id="req:1",
            fencing_token=gen_take,
            executor=taken,
            clock=clock_take,
            evidence_provider=_PROV,
        )


def test_b9_effect_gate_concurrent_single_execution(
    tmp_path: Path, migrated_db_template: Path
) -> None:
    from cortex_store._test_db_bootstrap import fresh_migrated_connection

    conn = fresh_migrated_connection(tmp_path, migrated_db_template)
    gen, intent = _setup(conn)
    path = next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")
    reached: list[str] = []
    gate = threading.Barrier(2)

    def _hook() -> None:
        reached.append(threading.current_thread().name)
        gate.wait(timeout=5)

    set_between_resolve_and_admit(_hook)
    counter = {"n": 0}

    def executor(permit: EffectPermit) -> dict:
        counter["n"] += 1
        return {"ok": True}

    results: list[object] = []
    errors: list[BaseException] = []

    def _run(name: str, request_id: str) -> None:
        local = _connect_wal(Path(path))
        local.execute("PRAGMA busy_timeout=2000")
        try:
            results.append(
                run_effect(
                    local,
                    intent_id=intent,
                    effect_kind="e",
                    request={"a": 1},
                    request_id=request_id,
                    fencing_token=gen,
                    executor=executor,
                    clock=FixedClock(T0),
                    evidence_provider=_PROV,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            local.close()

    threads = [
        threading.Thread(target=_run, args=("t1", "req:a"), name="t1"),
        threading.Thread(target=_run, args=("t2", "req:b"), name="t2"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    set_between_resolve_and_admit(None)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert len(reached) == 2
    assert sum(1 for item in results if getattr(item, "executed", False)) == 1
    assert counter["n"] == 1
    assert conn.execute("SELECT COUNT(*) FROM concern_effects").fetchone()[0] == 1


def _imports_effect_gate(source: str, *, constants: bool = True) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            "effect_gate" in alias.name for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "effect_gate" for alias in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and "effect_gate" in node.module
        ):
            return True
        if (
            constants
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "effect_gate" in node.value
        ):
            return True
    return False


def test_b10_effect_gate_not_imported_by_production() -> None:
    assert _imports_effect_gate("from . import effect_gate\n")
    assert _imports_effect_gate(
        "from cortex_store.concern_aggregate import effect_gate\n"
    )
    assert _imports_effect_gate(
        'import importlib\nimportlib.import_module("effect_gate")\n'
    )
    root = Path(__file__).resolve().parents[4]
    for base in (root / "libs", root / "services"):
        for path in base.rglob("*.py"):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            assert (
                _imports_effect_gate(
                    path.read_text(), constants=path.name != "effect_gate.py"
                )
                is False
            )


def test_b11_concurrent_evidenced_resend_single_permit(
    tmp_path: Path, migrated_db_template: Path
) -> None:
    from cortex_store._test_db_bootstrap import fresh_migrated_connection

    conn = fresh_migrated_connection(tmp_path, migrated_db_template)
    gen, intent = _setup(conn)
    admitted = admit_effect(
        conn,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen,
        now=T0,
    )
    path = next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")
    outcomes: list[object] = []

    def _run(request_id: str) -> None:
        local = sqlite3.connect(path, isolation_level=None, timeout=5)
        local.execute("PRAGMA journal_mode=WAL")
        local.execute("PRAGMA busy_timeout=5000")
        try:
            outcomes.append(
                admit_effect(
                    local,
                    intent_id=intent,
                    effect_kind="e",
                    request={"a": 1},
                    request_id=request_id,
                    fencing_token=gen,
                    now=T0 + timedelta(seconds=2),
                    resend_basis="resource_dedup",
                    resend_evidence=_ev(
                        "resource_dedup", admitted.effect_key, T0 + timedelta(seconds=1)
                    ),
                    evidence_provider=_PROV,
                )
            )
        except RetryBudgetExceededError as exc:
            outcomes.append(exc)
        finally:
            local.close()

    threads = [
        threading.Thread(target=_run, args=(rid,)) for rid in ("req:r1", "req:r2")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    permits = [item for item in outcomes if getattr(item, "execution_allowed", False)]
    refusals = [item for item in outcomes if isinstance(item, RetryBudgetExceededError)]
    assert len(permits) == 1 and len(refusals) == 1


def test_m1_failed_evidence_door(concern_db: sqlite3.Connection) -> None:
    """G1: failed without verified bound evidence writes nothing."""
    gen, intent = _setup(concern_db, "concern:m1door")
    admitted = admit_effect(
        concern_db,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen,
        now=T0,
    )
    snap = concern_db.execute("SELECT * FROM concern_effects").fetchone()
    later = T0 + timedelta(seconds=2)
    with pytest.raises(ValueError):
        record_outcome(
            concern_db,
            effect_key=admitted.effect_key,
            outcome=STATE_FAILED,
            receipt=None,
            fencing_token=gen,
            now=later,
        )
    with pytest.raises(ValueError):
        record_outcome(
            concern_db,
            effect_key=admitted.effect_key,
            outcome=STATE_SUCCEEDED,
            receipt=None,
            fencing_token=gen,
            now=later,
            evidence=_ev("not_applied", admitted.effect_key, later, "req:1"),
            evidence_provider=_PROV,
        )
    with pytest.raises(ValueError):
        record_outcome(
            concern_db,
            effect_key=admitted.effect_key,
            outcome=STATE_OUTCOME_UNKNOWN,
            receipt=None,
            fencing_token=gen,
            now=later,
            evidence=_ev("not_applied", admitted.effect_key, later, "req:1"),
            evidence_provider=_PROV,
        )
    with pytest.raises(EvidenceRejectedError):
        record_outcome(
            concern_db,
            effect_key=admitted.effect_key,
            outcome=STATE_FAILED,
            receipt=None,
            fencing_token=gen,
            now=later,
            evidence=_ev("not_applied", admitted.effect_key, later, "req:other"),
            evidence_provider=_PROV,
        )
    with pytest.raises(EvidenceRejectedError):
        record_outcome(
            concern_db,
            effect_key=admitted.effect_key,
            outcome=STATE_FAILED,
            receipt=None,
            fencing_token=gen,
            now=later,
            evidence=_ev("not_applied", admitted.effect_key, T0, "req:1"),
            evidence_provider=_PROV,
        )
    assert (
        concern_db.execute("SELECT state FROM concern_effects").fetchone()[0]
        == STATE_ADMITTED
    )
    assert concern_db.execute("SELECT * FROM concern_effects").fetchone() == snap
    good = _ev("not_applied", admitted.effect_key, later, "req:1")
    record_outcome(
        concern_db,
        effect_key=admitted.effect_key,
        outcome=STATE_FAILED,
        receipt=None,
        fencing_token=gen,
        now=later,
        evidence=good,
        evidence_provider=_PROV,
    )
    attempts = json.loads(
        concern_db.execute("SELECT attempts_json FROM concern_effects").fetchone()[0]
    )
    evidence_entries = [
        item for item in attempts if item.get("kind") == "outcome_evidence"
    ]
    assert evidence_entries[-1]["outcome"] == "failed"
    assert evidence_entries[-1]["evidence"] == good.to_json()
    assert evidence_entries[-1]["at"] == _iso_utc(later)


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def verify(self, evidence: EffectEvidence) -> bool:
        self.calls += 1
        return True


def test_m5_resend_state_and_provider_name(concern_db: sqlite3.Connection) -> None:
    """Resend off admitted is InvalidTransitionError. Name mismatch never calls verify.

    A resend while outcome_unknown stays ReconcileRequiredError: it fails closed.
    """
    gen, intent = _setup(concern_db, "concern:m5")
    admitted = admit_effect(
        concern_db,
        intent_id=intent,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen,
        now=T0,
    )
    record_outcome(
        concern_db,
        effect_key=admitted.effect_key,
        outcome=STATE_FAILED,
        receipt=None,
        fencing_token=gen,
        now=T0 + timedelta(seconds=2),
        evidence=_ev(
            "not_applied", admitted.effect_key, T0 + timedelta(seconds=2), "req:1"
        ),
        evidence_provider=_PROV,
    )
    with pytest.raises(InvalidTransitionError):
        admit_effect(
            concern_db,
            intent_id=intent,
            effect_kind="e",
            request={"a": 1},
            request_id="req:r",
            fencing_token=gen,
            now=T0 + timedelta(seconds=3),
            resend_basis="resource_dedup",
            resend_evidence=_ev(
                "resource_dedup", admitted.effect_key, T0 + timedelta(seconds=3)
            ),
            evidence_provider=_PROV,
        )
    gen2, intent2 = _setup(concern_db, "concern:m5b")
    row = admit_effect(
        concern_db,
        intent_id=intent2,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen2,
        now=T0,
    )
    record_outcome(
        concern_db,
        effect_key=row.effect_key,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=gen2,
        now=T0,
    )
    with pytest.raises(ReconcileRequiredError):
        admit_effect(
            concern_db,
            intent_id=intent2,
            effect_kind="e",
            request={"a": 1},
            request_id="req:unk",
            fencing_token=gen2,
            now=T0 + timedelta(seconds=2),
            resend_basis="resource_dedup",
            resend_evidence=_ev(
                "resource_dedup", row.effect_key, T0 + timedelta(seconds=1)
            ),
            evidence_provider=_PROV,
        )
    reconcile_effect(
        concern_db,
        effect_key=row.effect_key,
        observed_applied=False,
        now=T0 + timedelta(seconds=2),
        evidence=_ev("not_applied", row.effect_key, T0 + timedelta(seconds=2), "req:1"),
        evidence_provider=_PROV,
    )
    with pytest.raises(InvalidTransitionError):
        admit_effect(
            concern_db,
            intent_id=intent2,
            effect_kind="e",
            request={"a": 1},
            request_id="req:rr",
            fencing_token=gen2,
            now=T0 + timedelta(seconds=3),
            resend_basis="resource_dedup",
            resend_evidence=_ev(
                "resource_dedup", row.effect_key, T0 + timedelta(seconds=3)
            ),
            evidence_provider=_PROV,
        )
    mismatch = _Named("other")
    gen3, intent3 = _setup(concern_db, "concern:m5c")
    live = admit_effect(
        concern_db,
        intent_id=intent3,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=gen3,
        now=T0,
    )
    with pytest.raises(EvidenceRejectedError):
        admit_effect(
            concern_db,
            intent_id=intent3,
            effect_kind="e",
            request={"a": 1},
            request_id="req:name",
            fencing_token=gen3,
            now=T0,
            resend_basis="resource_dedup",
            resend_evidence=_ev("resource_dedup", live.effect_key, T0),
            evidence_provider=mismatch,
        )
    assert mismatch.calls == 0
