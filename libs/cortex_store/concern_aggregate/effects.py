"""Phase-3 effect ledger: intent identity, effect keys, leases, fencing, holds.

This module is **not** derived from ``concern_intents`` / journal kind ``action_intent``:
those remain the Phase-1 fold view (``concern_intents``). Action-intent rows here use
``concern_action_intents`` and mint ``int:`` ids only in this table — no join bridge.

Lease-scoped ``owner_generation`` fences effects per ``(concern_id, work_key)``; it does
not reuse ``concern_head.fencing_generation`` (concern-scoped).

Ledger tables are **not** rebuilt by ``rebuild_concern``: recovery clears materialized
views and replays the journal; effect rows, intents, and leases persist unchanged.
Holds are journaled via ``control_set`` / ``control_clear`` so they survive rebuild.

Ref: decision a:42489 (admission and identities).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import fault_injection
from .canonical_json import jcs_serialize
from .canonical_json import material_fingerprint as compute_material_fingerprint
from .identities import as_intent_id, as_request_id
from .journal import TransitionRejectedError, append_transition, resolve_authorized_by

EFFECT_KEY_NAMESPACE = "ulg.concern.effect.v1"

RECOGNIZED_HOLD_KEYS = frozenset({"stop_new_admissions", "cancel", "drain"})

INTENT_OPEN = "open"
INTENT_HELD = "held"

STATE_ADMITTED = "admitted"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_OUTCOME_UNKNOWN = "outcome_unknown"
STATE_RECONCILE_REQUIRED_RETRY = "reconcile_required_retry"


class EffectAdmissionError(Exception):
    """Base class for effect admission / lease / fencing failures (slice 3a)."""


class EffectConflictError(EffectAdmissionError):
    """Same effect key with a different material fingerprint (a:42489)."""


class StaleFencingTokenError(EffectAdmissionError):
    """Presented fencing token does not match the current lease owner generation."""


class LeaseHeldError(EffectAdmissionError):
    """Another holder holds an unexpired lease for this work key."""


class LeaseExpiredError(EffectAdmissionError):
    """Renew or outcome recorded against an expired lease when disallowed."""


class HoldActiveError(EffectAdmissionError):
    """A recognized admission hold is active; intent may be marked held."""

    def __init__(self, message: str, *, intent_id: str) -> None:
        super().__init__(message)
        self.intent_id = intent_id


class ReconcileRequiredError(EffectAdmissionError):
    """Effect needs reconcile before further admission (unknown outcome or exhausted retry)."""


class RetryBudgetExceededError(EffectAdmissionError):
    """Automatic transport retries exhausted for an admitted effect."""

    def __init__(self, message: str, *, effect_key: str) -> None:
        super().__init__(message)
        self.effect_key = effect_key


class InvalidTransitionError(EffectAdmissionError):
    """Disallowed state transition on an effect."""

    def __init__(self, message: str, *, effect_key: str | None = None) -> None:
        super().__init__(message)
        self.effect_key = effect_key


class StaleRunGenerationError(EffectAdmissionError):
    """Caller supplied a run_generation lower than the maximum for the triple."""


def compute_effect_key(
    *,
    concern_id: str,
    work_key: str,
    intent_id: str,
    effect_kind: str,
) -> str:
    """Return the hex sha256 effect key for (namespace, ids, kind) via JCS(a:42489)."""
    payload = [EFFECT_KEY_NAMESPACE, concern_id, work_key, intent_id, effect_kind]
    digest = hashlib.sha256(jcs_serialize(payload).encode("utf-8")).hexdigest()
    return digest


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def _parse_iso_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def resolve_action_intent(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    work_key: str,
    action_slot: str,
    run_generation: int,
    now: datetime,
) -> str:
    """Create or return the canonical intent row for the tuple; enforces run monotonicity."""
    if run_generation < 0:
        raise StaleRunGenerationError("run_generation must be non-negative")
    iid_t = as_intent_id  # validates prefix
    del iid_t
    now_s = _iso_utc(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT intent_id FROM concern_action_intents "
            "WHERE concern_id = ? AND work_key = ? AND action_slot = ? AND run_generation = ?",
            (concern_id, work_key, action_slot, run_generation),
        ).fetchone()
        if row is not None:
            conn.commit()
            return str(row[0])

        max_row = conn.execute(
            "SELECT MAX(run_generation) FROM concern_action_intents "
            "WHERE concern_id = ? AND work_key = ? AND action_slot = ?",
            (concern_id, work_key, action_slot),
        ).fetchone()
        if max_row[0] is not None and run_generation < int(max_row[0]):
            conn.rollback()
            raise StaleRunGenerationError(
                f"run_generation {run_generation} < max {int(max_row[0])} for triple"
            )

        intent_id = f"int:{uuid.uuid4().hex}"
        as_intent_id(intent_id)
        conn.execute(
            "INSERT INTO concern_action_intents "
            "(concern_id, work_key, action_slot, run_generation, intent_id, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                concern_id,
                work_key,
                action_slot,
                run_generation,
                intent_id,
                INTENT_OPEN,
                now_s,
                now_s,
            ),
        )
        conn.commit()
        return intent_id
    except Exception:
        conn.rollback()
        raise


def _sweep_admitted_to_unknown(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    work_key: str,
    new_generation: int,
    now: datetime,
) -> None:
    now_s = _iso_utc(now)
    conn.execute(
        "UPDATE concern_effects SET state = ?, updated_at = ? "
        "WHERE concern_id = ? AND work_key = ? AND state = ? AND fencing_token < ?",
        (
            STATE_OUTCOME_UNKNOWN,
            now_s,
            concern_id,
            work_key,
            STATE_ADMITTED,
            new_generation,
        ),
    )


def acquire_lease(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    work_key: str,
    holder: str,
    lease_expires_at: datetime,
    now: datetime,
) -> int:
    """Acquire or refresh a per-work-key lease; increments generation when ownership changes."""
    exp_s = _iso_utc(lease_expires_at)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT holder, owner_generation, lease_expires_at FROM concern_effect_leases "
            "WHERE concern_id = ? AND work_key = ?",
            (concern_id, work_key),
        ).fetchone()

        if row is None:
            gen = 1
            _sweep_admitted_to_unknown(
                conn,
                concern_id=concern_id,
                work_key=work_key,
                new_generation=gen,
                now=now,
            )
            conn.execute(
                "INSERT INTO concern_effect_leases (concern_id, work_key, holder, owner_generation, lease_expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (concern_id, work_key, holder, gen, exp_s),
            )
            conn.commit()
            return gen

        prev_holder, prev_gen, prev_exp_s = str(row[0]), int(row[1]), str(row[2])
        prev_exp = _parse_iso_utc(prev_exp_s)
        if now <= prev_exp:
            if prev_holder == holder:
                conn.execute(
                    "UPDATE concern_effect_leases SET lease_expires_at = ? "
                    "WHERE concern_id = ? AND work_key = ?",
                    (exp_s, concern_id, work_key),
                )
                conn.commit()
                return prev_gen
            conn.rollback()
            raise LeaseHeldError(
                f"lease held by {prev_holder!r} for concern={concern_id!r} work_key={work_key!r}"
            )

        new_gen = prev_gen + 1
        _sweep_admitted_to_unknown(
            conn,
            concern_id=concern_id,
            work_key=work_key,
            new_generation=new_gen,
            now=now,
        )
        conn.execute(
            "UPDATE concern_effect_leases SET holder = ?, owner_generation = ?, lease_expires_at = ? "
            "WHERE concern_id = ? AND work_key = ?",
            (holder, new_gen, exp_s, concern_id, work_key),
        )
        conn.commit()
        return new_gen
    except Exception:
        conn.rollback()
        raise


def renew_lease(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    work_key: str,
    holder: str,
    fencing_token: int,
    lease_expires_at: datetime,
    now: datetime,
) -> None:
    """Extend expiry for the same holder and generation while the lease is unexpired."""
    exp_s = _iso_utc(lease_expires_at)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT holder, owner_generation, lease_expires_at FROM concern_effect_leases "
            "WHERE concern_id = ? AND work_key = ?",
            (concern_id, work_key),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise LeaseExpiredError("no lease row to renew")
        prev_holder, prev_gen, prev_exp_s = str(row[0]), int(row[1]), str(row[2])
        prev_exp = _parse_iso_utc(prev_exp_s)
        if now > prev_exp:
            conn.rollback()
            raise LeaseExpiredError("lease already expired at renew")
        if prev_holder != holder or prev_gen != fencing_token:
            conn.rollback()
            raise StaleFencingTokenError("renew: holder or generation mismatch")
        conn.execute(
            "UPDATE concern_effect_leases SET lease_expires_at = ? WHERE concern_id = ? AND work_key = ?",
            (exp_s, concern_id, work_key),
        )
        conn.commit()
    except EffectAdmissionError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise


def _load_intent_row(
    conn: sqlite3.Connection, intent_id: str
) -> tuple[str, str, str, int]:
    row = conn.execute(
        "SELECT concern_id, work_key, action_slot, run_generation FROM concern_action_intents WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()
    if row is None:
        raise InvalidTransitionError(
            f"unknown intent_id {intent_id!r}", effect_key=None
        )
    return str(row[0]), str(row[1]), str(row[2]), int(row[3])


def _lease_row(
    conn: sqlite3.Connection, concern_id: str, work_key: str
) -> tuple[str, int, str] | None:
    row = conn.execute(
        "SELECT holder, owner_generation, lease_expires_at FROM concern_effect_leases "
        "WHERE concern_id = ? AND work_key = ?",
        (concern_id, work_key),
    ).fetchone()
    if row is None:
        return None
    return str(row[0]), int(row[1]), str(row[2])


def _recognized_hold_active(conn: sqlite3.Connection, concern_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM concern_controls WHERE concern_id = ? AND control_key IN (?, ?, ?) LIMIT 1",
        (concern_id, "stop_new_admissions", "cancel", "drain"),
    ).fetchone()
    return row is not None


def _parse_json_list(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _dump_json_list(items: list[dict[str, Any]]) -> str:
    return json.dumps(items, sort_keys=True, separators=(",", ":"))


def _automatic_retry_allowed(attempts: list[dict[str, Any]]) -> bool:
    last_bi = -1
    for i, e in enumerate(attempts):
        if e.get("kind") in ("initial", "decision"):
            last_bi = i
    tail = attempts[last_bi + 1 :]
    return not any(e.get("kind") == "auto_retry" for e in tail)


def _find_unconsumed_retry(recovery: list[dict[str, Any]]) -> int | None:
    for i in range(len(recovery) - 1, -1, -1):
        e = recovery[i]
        if e.get("decision") == "retry" and not e.get("consumed_attempt"):
            return i
    return None


@dataclass(frozen=True)
class OutcomeResult:
    """Result of ``record_outcome`` when a late report is recorded without raising."""

    applied: bool
    late_report: bool


def admit_effect(
    conn: sqlite3.Connection,
    *,
    intent_id: str,
    effect_kind: str,
    request: dict[str, Any],
    request_id: str,
    fencing_token: int,
    now: datetime,
) -> dict[str, Any]:
    """Admit one effect attempt under lease fencing and hold policy; may replay succeeded."""
    as_request_id(request_id)
    intent_id = str(as_intent_id(intent_id))
    concern_id, work_key, _action_slot, _rg = _load_intent_row(conn, intent_id)
    fp = compute_material_fingerprint(request)
    effect_key = compute_effect_key(
        concern_id=concern_id,
        work_key=work_key,
        intent_id=intent_id,
        effect_kind=effect_kind,
    )
    now_s = _iso_utc(now)

    fault_injection.maybe_between_resolve_and_admit()

    conn.execute("BEGIN IMMEDIATE")
    fault_injection.maybe_after_begin()
    try:
        eff_row = conn.execute(
            "SELECT material_fingerprint, state, attempt_count, attempts_json, receipt_json, recovery_json, fencing_token "
            "FROM concern_effects WHERE effect_key = ?",
            (effect_key,),
        ).fetchone()

        if eff_row is not None:
            stored_fp = str(eff_row[0])
            if stored_fp != fp:
                conn.rollback()
                raise EffectConflictError(
                    "material fingerprint mismatch for effect key"
                )

            st = str(eff_row[1])
            if st == STATE_SUCCEEDED:
                receipt = json.loads(eff_row[4]) if eff_row[4] else {}
                conn.commit()
                return {"replay": True, "effect_key": effect_key, "receipt": receipt}

        lease = _lease_row(conn, concern_id, work_key)
        if lease is None:
            conn.rollback()
            raise StaleFencingTokenError("no lease row for fencing check")
        _holder, cur_gen, exp_s = lease
        exp = _parse_iso_utc(exp_s)
        if fencing_token != cur_gen:
            conn.rollback()
            raise StaleFencingTokenError("fencing token mismatch")
        if now > exp:
            conn.rollback()
            raise StaleFencingTokenError("lease expired at admit")

        rows = conn.execute(
            "SELECT control_key FROM concern_controls WHERE concern_id = ? AND control_key IN (?, ?, ?)",
            (concern_id, "stop_new_admissions", "cancel", "drain"),
        ).fetchall()
        fault_injection.maybe_after_controls_read()
        if rows:
            conn.execute(
                "UPDATE concern_action_intents SET state = ?, updated_at = ? WHERE intent_id = ?",
                (INTENT_HELD, now_s, intent_id),
            )
            conn.commit()
            raise HoldActiveError("admission hold active", intent_id=intent_id)

        if eff_row is not None:
            attempts = _parse_json_list(eff_row[3])
            ac = int(eff_row[2])
            recovery = _parse_json_list(eff_row[5])

            if st == STATE_OUTCOME_UNKNOWN:
                conn.rollback()
                raise ReconcileRequiredError("admit blocked: outcome_unknown")

            if st in (STATE_FAILED, STATE_RECONCILE_REQUIRED_RETRY):
                if _find_unconsumed_retry(recovery) is None:
                    conn.rollback()
                    raise ReconcileRequiredError(
                        "admit blocked: recovery decision required"
                    )
                kind = "decision"
            elif st == STATE_ADMITTED:
                if not _automatic_retry_allowed(attempts):
                    conn.rollback()
                    raise RetryBudgetExceededError(
                        "automatic retry budget exhausted", effect_key=effect_key
                    )
                kind = "auto_retry"
            else:
                conn.rollback()
                raise InvalidTransitionError(
                    f"admit from state {st!r}", effect_key=effect_key
                )

            attempt_entry: dict[str, Any] = {
                "kind": kind,
                "request_id": request_id,
                "req": request_id,
                "at": now_s,
            }
            attempts.append(attempt_entry)
            ac += 1

            recovery_new = recovery
            if kind == "decision":
                idx = _find_unconsumed_retry(recovery)
                assert idx is not None
                recovery_new = list(recovery)
                recovery_new[idx] = dict(recovery_new[idx])
                recovery_new[idx]["consumed_attempt"] = True

            new_state = st
            if kind == "decision":
                new_state = STATE_ADMITTED
            conn.execute(
                "UPDATE concern_effects SET state = ?, attempt_count = ?, attempts_json = ?, recovery_json = ?, "
                "fencing_token = ?, updated_at = ? WHERE effect_key = ?",
                (
                    new_state,
                    ac,
                    _dump_json_list(attempts),
                    _dump_json_list(recovery_new),
                    fencing_token,
                    now_s,
                    effect_key,
                ),
            )
            conn.execute(
                "UPDATE concern_action_intents SET state = ?, updated_at = ? WHERE intent_id = ?",
                (INTENT_OPEN, now_s, intent_id),
            )
            conn.commit()
            return {"replay": False, "effect_key": effect_key, "receipt": None}

        attempts = [
            {
                "kind": "initial",
                "request_id": request_id,
                "req": request_id,
                "at": now_s,
            }
        ]
        conn.execute(
            "INSERT INTO concern_effects ("
            "effect_key, concern_id, work_key, intent_id, effect_kind, material_fingerprint, "
            "fencing_token, state, attempt_count, attempts_json, receipt_json, recovery_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                effect_key,
                concern_id,
                work_key,
                intent_id,
                effect_kind,
                fp,
                fencing_token,
                STATE_ADMITTED,
                1,
                _dump_json_list(attempts),
                None,
                "[]",
                now_s,
                now_s,
            ),
        )
        conn.execute(
            "UPDATE concern_action_intents SET state = ?, updated_at = ? WHERE intent_id = ?",
            (INTENT_OPEN, now_s, intent_id),
        )
        conn.commit()
        return {"replay": False, "effect_key": effect_key, "receipt": None}
    except EffectAdmissionError:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    except Exception:
        conn.rollback()
        raise


def record_outcome(
    conn: sqlite3.Connection,
    *,
    effect_key: str,
    outcome: str,
    receipt: dict[str, Any] | None,
    fencing_token: int,
    now: datetime,
) -> OutcomeResult:
    """Record terminal or unknown outcome; late reports commit without raising."""
    now_s = _iso_utc(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT concern_id, work_key, intent_id, state, attempts_json, receipt_json "
            "FROM concern_effects WHERE effect_key = ?",
            (effect_key,),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise InvalidTransitionError("effect row missing", effect_key=effect_key)

        concern_id, work_key = str(row[0]), str(row[1])
        st = str(row[3])
        attempts = _parse_json_list(row[4])

        lease = _lease_row(conn, concern_id, work_key)
        cur_gen = lease[1] if lease is not None else None

        if cur_gen is not None and fencing_token != cur_gen:
            attempts.append(
                {
                    "kind": "late_report",
                    "fencing_token": fencing_token,
                    "outcome": outcome,
                    "at": now_s,
                }
            )
            if st == STATE_ADMITTED:
                conn.execute(
                    "UPDATE concern_effects SET state = ?, attempts_json = ?, updated_at = ? WHERE effect_key = ?",
                    (
                        STATE_OUTCOME_UNKNOWN,
                        _dump_json_list(attempts),
                        now_s,
                        effect_key,
                    ),
                )
            elif st == STATE_SUCCEEDED:
                conn.execute(
                    "UPDATE concern_effects SET attempts_json = ?, updated_at = ? WHERE effect_key = ?",
                    (_dump_json_list(attempts), now_s, effect_key),
                )
            conn.commit()
            return OutcomeResult(applied=False, late_report=True)

        if st == STATE_SUCCEEDED:
            conn.rollback()
            raise InvalidTransitionError(
                "outcome on succeeded terminal", effect_key=effect_key
            )

        if outcome not in (STATE_SUCCEEDED, STATE_FAILED, STATE_OUTCOME_UNKNOWN):
            conn.rollback()
            raise InvalidTransitionError(
                f"unknown outcome {outcome!r}", effect_key=effect_key
            )

        receipt_json = (
            json.dumps(receipt, sort_keys=True) if receipt is not None else None
        )
        new_state = outcome
        conn.execute(
            "UPDATE concern_effects SET state = ?, receipt_json = ?, attempts_json = ?, updated_at = ? "
            "WHERE effect_key = ?",
            (new_state, receipt_json, _dump_json_list(attempts), now_s, effect_key),
        )
        conn.commit()
        return OutcomeResult(applied=True, late_report=False)
    except EffectAdmissionError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise


def reconcile_effect(
    conn: sqlite3.Connection,
    *,
    effect_key: str,
    observed_applied: bool,
    now: datetime,
) -> None:
    """Reconcile an ``outcome_unknown`` effect to succeeded or reconcile_required_retry."""
    now_s = _iso_utc(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT state FROM concern_effects WHERE effect_key = ?", (effect_key,)
        ).fetchone()
        if row is None:
            conn.rollback()
            raise InvalidTransitionError("effect row missing", effect_key=effect_key)
        st = str(row[0])
        if st != STATE_OUTCOME_UNKNOWN:
            conn.rollback()
            raise InvalidTransitionError(
                f"reconcile only from outcome_unknown, got {st!r}",
                effect_key=effect_key,
            )
        new_state = (
            STATE_SUCCEEDED if observed_applied else STATE_RECONCILE_REQUIRED_RETRY
        )
        conn.execute(
            "UPDATE concern_effects SET state = ?, updated_at = ? WHERE effect_key = ?",
            (new_state, now_s, effect_key),
        )
        conn.commit()
    except EffectAdmissionError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise


def record_recovery_decision(
    conn: sqlite3.Connection,
    *,
    effect_key: str,
    decision: str,
    decided_by: str,
    reason: str,
    now: datetime,
) -> None:
    """Persist a consumable recovery decision (``retry``) on failed / reconcile_required_retry."""
    now_s = _iso_utc(now)
    if decision != "retry":
        raise InvalidTransitionError(
            "only decision='retry' is supported in slice 3a", effect_key=effect_key
        )
    resolve_authorized_by(conn, decided_by)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT state, recovery_json FROM concern_effects WHERE effect_key = ?",
            (effect_key,),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise InvalidTransitionError("effect row missing", effect_key=effect_key)
        st = str(row[0])
        if st not in (STATE_FAILED, STATE_RECONCILE_REQUIRED_RETRY):
            conn.rollback()
            raise InvalidTransitionError(
                f"recovery decision only from failed / reconcile_required_retry, got {st!r}",
                effect_key=effect_key,
            )
        recovery = _parse_json_list(row[1])
        recovery.append(
            {
                "decision": decision,
                "decided_by": decided_by,
                "reason": reason,
                "decided_at": now_s,
                "consumed_attempt": False,
            }
        )
        conn.execute(
            "UPDATE concern_effects SET recovery_json = ?, updated_at = ? WHERE effect_key = ?",
            (_dump_json_list(recovery), now_s, effect_key),
        )
        conn.commit()
    except TransitionRejectedError:
        conn.rollback()
        raise
    except EffectAdmissionError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise


def lookup_receipt(conn: sqlite3.Connection, effect_key: str) -> dict[str, Any] | None:
    """Return the stored receipt JSON for *effect_key*, if any."""
    row = conn.execute(
        "SELECT receipt_json FROM concern_effects WHERE effect_key = ?", (effect_key,)
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return json.loads(str(row[0]))


def set_hold(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    control_key: str,
    hold: dict[str, Any],
    authorized_by: str,
    record_ref: str,
) -> None:
    """Journal a ``control_set`` hold; rejects unknown *control_key* before journal I/O."""
    if control_key not in RECOGNIZED_HOLD_KEYS:
        raise ValueError(f"unsupported control_key for hold: {control_key!r}")
    if conn.in_transaction:
        raise RuntimeError(
            "set_hold requires a connection outside an active transaction"
        )
    append_transition(
        conn,
        concern_id=concern_id,
        kind="control_set",
        payload={"control_key": control_key, "hold": hold},
        record_ref=record_ref,
        authorized_by=authorized_by,
        emit_advisory=False,
    )


def clear_hold(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    control_key: str,
    authorized_by: str,
    record_ref: str,
) -> None:
    """Journal ``control_clear`` and reopen ``held`` intents when no hold keys remain."""
    if control_key not in RECOGNIZED_HOLD_KEYS:
        raise ValueError(f"unsupported control_key for clear: {control_key!r}")
    if conn.in_transaction:
        raise RuntimeError(
            "clear_hold requires a connection outside an active transaction"
        )
    append_transition(
        conn,
        concern_id=concern_id,
        kind="control_clear",
        payload={"control_key": control_key},
        record_ref=record_ref,
        authorized_by=authorized_by,
        emit_advisory=False,
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _recognized_hold_active(conn, concern_id):
            conn.execute(
                "UPDATE concern_action_intents SET state = ?, updated_at = ? "
                "WHERE concern_id = ? AND state = ?",
                (INTENT_OPEN, _iso_utc(datetime.now(tz=UTC)), concern_id, INTENT_HELD),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
