"""Phase-3 effect ledger: intent identity, effect keys, leases, fencing, holds.

This module is **not** derived from ``concern_intents`` / journal kind ``action_intent``:
those remain the Phase-1 fold view (``concern_intents``). Action-intent rows here use
``concern_action_intents`` and mint ``int:`` ids only in this table — no join bridge.

Lease-scoped ``owner_generation`` fences effects per ``(concern_id, work_key)``; it does
not reuse ``concern_head.fencing_generation`` (concern-scoped).

Ledger tables are **not** rebuilt by ``rebuild_concern``: recovery clears materialized
views and replays the journal; effect rows, intents, and leases persist unchanged.
Holds are journaled via ``control_set`` / ``control_clear`` so they survive rebuild.

``attempt_count`` / ``attempts_json`` are transport accounting only; they do not count
external effect executions. One ``concern_effects`` row is not proof of one external
effect. ``execution_allowed`` on ``AdmitResult`` is the sole permit to run the side
effect, and only for ``permit_request_id``.

Library ``now`` is caller-asserted. Trust holds only when slice 3b.2 routes
callers through the test-only adapter clock.

Ref: decision a:42489 (admission and identities); R1 execution permission (root review).
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
from .clock import require_aware_utc
from .evidence import EffectEvidence, EvidenceProvider
from .identities import as_intent_id, as_request_id
from .journal import TransitionRejectedError, append_transition, resolve_authorized_by

EFFECT_KEY_NAMESPACE = "ulg.concern.effect.v1"

RECOGNIZED_HOLD_KEYS = frozenset({"stop_new_admissions", "cancel", "drain"})
VALID_RESEND_BASES = frozenset({"resource_dedup", "confirmed_not_accepted"})

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


class EvidenceRejectedError(EffectAdmissionError):
    """Verified-evidence check failed; the ledger wrote nothing."""


_STATE_WRITE_COLUMNS = frozenset(
    {"attempt_count", "attempts_json", "recovery_json", "receipt_json", "fencing_token"}
)

# The 3a automatic-retry admit (evidenced resend from admitted) writes state.
# That writer passes via ``auto_retry``. No caller passes via ``resend``.
ALLOWED_TRANSITIONS: frozenset[tuple[str, str, str]] = frozenset(
    {
        (STATE_ADMITTED, STATE_ADMITTED, "auto_retry"),
        (STATE_FAILED, STATE_ADMITTED, "decision"),
        (STATE_RECONCILE_REQUIRED_RETRY, STATE_ADMITTED, "decision"),
        (STATE_ADMITTED, STATE_SUCCEEDED, "outcome"),
        (STATE_ADMITTED, STATE_FAILED, "outcome"),
        (STATE_ADMITTED, STATE_OUTCOME_UNKNOWN, "outcome"),
        (STATE_ADMITTED, STATE_OUTCOME_UNKNOWN, "late_report"),
        (STATE_ADMITTED, STATE_OUTCOME_UNKNOWN, "sweep"),
        (STATE_OUTCOME_UNKNOWN, STATE_SUCCEEDED, "reconcile"),
        (STATE_OUTCOME_UNKNOWN, STATE_RECONCILE_REQUIRED_RETRY, "reconcile"),
    }
)


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
    """Format an aware datetime as whole-second UTC ISO. Naive values raise."""
    aware = require_aware_utc(dt, name="datetime")
    return aware.replace(microsecond=0).isoformat()


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
    now = require_aware_utc(now, name="now")
    if run_generation < 0:
        raise StaleRunGenerationError("run_generation must be non-negative")
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


def _set_state(
    conn: sqlite3.Connection,
    *,
    effect_key: str,
    from_state: str,
    to_state: str,
    via: str,
    now_s: str,
    columns: dict[str, Any] | None = None,
) -> None:
    """Assign ``state`` only for a labeled triple, guarded by the prior state.

    Column names come from a fixed allowlist so caller strings are never interpolated.
    """
    extra = columns or {}
    unknown = set(extra) - _STATE_WRITE_COLUMNS
    if unknown:
        raise ValueError(f"state write column not allowlisted: {sorted(unknown)!r}")
    if (from_state, to_state, via) not in ALLOWED_TRANSITIONS:
        raise InvalidTransitionError(
            f"transition {from_state!r} -> {to_state!r} via {via!r} is not allowed",
            effect_key=effect_key,
        )
    names = sorted(extra)
    assignments = ", ".join(f"{name} = ?" for name in names)
    set_sql = "state = ?, updated_at = ?"
    if assignments:
        set_sql = f"{set_sql}, {assignments}"
    params: list[Any] = [
        to_state,
        now_s,
        *[extra[name] for name in names],
        effect_key,
        from_state,
    ]
    cur = conn.execute(
        f"UPDATE concern_effects SET {set_sql} WHERE effect_key = ? AND state = ?",
        params,
    )
    if cur.rowcount != 1:
        raise InvalidTransitionError(
            f"state guard missed for {effect_key!r} from {from_state!r}",
            effect_key=effect_key,
        )


def _check_evidence(
    evidence: EffectEvidence | None,
    provider: EvidenceProvider | None,
    *,
    expected_kinds: frozenset[str],
    effect_key: str,
    now: datetime,
) -> EffectEvidence:
    """Reject unverified evidence before BEGIN so a failed check writes nothing."""
    if evidence is None or provider is None:
        raise ValueError("evidence and evidence_provider are required")
    if not isinstance(evidence, EffectEvidence):
        raise ValueError("evidence must be an EffectEvidence")
    evidence.validate_shape()
    if evidence.kind not in expected_kinds:
        raise ValueError(
            f"evidence kind {evidence.kind!r} not in {sorted(expected_kinds)!r}"
        )
    if evidence.effect_key != effect_key:
        raise EvidenceRejectedError(
            "evidence effect_key does not match",
        )
    if require_aware_utc(evidence.observed_at, name="observed_at") > require_aware_utc(
        now, name="now"
    ):
        raise EvidenceRejectedError("evidence observed_at is after now")
    if getattr(provider, "name", None) != evidence.provider:
        raise EvidenceRejectedError("provider name does not match evidence.provider")
    if provider.verify(evidence) is not True:
        raise EvidenceRejectedError("evidence provider rejected the record")
    return evidence


def _latest_permit_attempt(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in reversed(attempts):
        if entry.get("kind") in ("initial", "decision", "auto_retry"):
            return entry
    return None


def _bind_non_application(
    evidence: EffectEvidence, attempts: list[dict[str, Any]]
) -> None:
    """Bind non-application evidence to the latest permit inside the write transaction."""
    permit = _latest_permit_attempt(attempts)
    if permit is None or evidence.superseded_request_id != permit.get("request_id"):
        raise EvidenceRejectedError(
            "evidence is not bound to the latest permit request_id"
        )
    permit_at = _parse_iso_utc(str(permit.get("at")))
    observed = _parse_iso_utc(_iso_utc(evidence.observed_at))
    if observed <= permit_at:
        raise EvidenceRejectedError(
            "evidence observed_at is not after the latest permit"
        )


def _sweep_admitted_to_unknown(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    work_key: str,
    new_generation: int,
    now: datetime,
) -> None:
    now_s = _iso_utc(now)
    rows = conn.execute(
        "SELECT effect_key FROM concern_effects "
        "WHERE concern_id = ? AND work_key = ? AND state = ? AND fencing_token < ?",
        (concern_id, work_key, STATE_ADMITTED, new_generation),
    ).fetchall()
    for (effect_key,) in rows:
        _set_state(
            conn,
            effect_key=str(effect_key),
            from_state=STATE_ADMITTED,
            to_state=STATE_OUTCOME_UNKNOWN,
            via="sweep",
            now_s=now_s,
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
    now = require_aware_utc(now, name="now")
    lease_expires_at = require_aware_utc(lease_expires_at, name="lease_expires_at")
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
    now = require_aware_utc(now, name="now")
    lease_expires_at = require_aware_utc(lease_expires_at, name="lease_expires_at")
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
    keys = sorted(RECOGNIZED_HOLD_KEYS)
    placeholders = ", ".join("?" for _ in keys)
    row = conn.execute(
        f"SELECT 1 FROM concern_controls WHERE concern_id = ? AND control_key IN ({placeholders}) LIMIT 1",
        (concern_id, *keys),
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


@dataclass(frozen=True)
class AdmitResult:
    """Admission outcome; only ``execution_allowed`` authorizes an external effect for ``permit_request_id``."""

    effect_key: str
    intent_id: str
    state: str
    replay: bool
    receipt: dict[str, Any] | None
    execution_allowed: bool
    permit_request_id: str | None


def _validate_resend_params(
    resend_basis: str | None,
    resend_evidence: EffectEvidence | None,
    evidence_provider: EvidenceProvider | None,
) -> None:
    present = (
        resend_basis is not None
        or resend_evidence is not None
        or evidence_provider is not None
    )
    if not present:
        return
    if resend_basis is None or resend_evidence is None or evidence_provider is None:
        raise ValueError(
            "resend_basis, resend_evidence, and evidence_provider must be provided together"
        )
    if resend_basis not in VALID_RESEND_BASES:
        raise ValueError(f"unsupported resend_basis: {resend_basis!r}")


def admit_effect(
    conn: sqlite3.Connection,
    *,
    intent_id: str,
    effect_kind: str,
    request: dict[str, Any],
    request_id: str,
    fencing_token: int,
    now: datetime,
    resend_basis: str | None = None,
    resend_evidence: EffectEvidence | None = None,
    evidence_provider: EvidenceProvider | None = None,
) -> AdmitResult:
    """Admit one effect under fencing and holds.

    ``execution_allowed`` is true only for the inserting admit, an admit that consumes
    an unconsumed retry decision, or an evidenced resend from ``admitted``. A resend
    while the row is ``outcome_unknown`` raises ``ReconcileRequiredError`` (fails
    closed; not ``InvalidTransitionError``). A repeat
    admit with the same fingerprint and a current token writes nothing and returns
    ``execution_allowed`` false. ``succeeded`` replays the stored receipt (``None`` when
    the column is null) and never permits execution.
    """
    now = require_aware_utc(now, name="now")
    _validate_resend_params(resend_basis, resend_evidence, evidence_provider)
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
    if resend_basis is not None:
        assert resend_evidence is not None and evidence_provider is not None
        if not isinstance(resend_evidence, EffectEvidence):
            raise ValueError("resend_evidence must be an EffectEvidence")
        if resend_evidence.kind != resend_basis:
            raise ValueError("resend_evidence.kind must equal resend_basis")
        _check_evidence(
            resend_evidence,
            evidence_provider,
            expected_kinds=frozenset({resend_basis}),
            effect_key=effect_key,
            now=now,
        )

    fault_injection.maybe_between_resolve_and_admit()

    conn.execute("BEGIN IMMEDIATE")
    try:
        fault_injection.maybe_after_begin()
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
                receipt = json.loads(eff_row[4]) if eff_row[4] else None
                conn.commit()
                return AdmitResult(
                    effect_key=effect_key,
                    intent_id=intent_id,
                    state=STATE_SUCCEEDED,
                    replay=True,
                    receipt=receipt,
                    execution_allowed=False,
                    permit_request_id=None,
                )

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

        hold_keys = sorted(RECOGNIZED_HOLD_KEYS)
        hold_ph = ", ".join("?" for _ in hold_keys)
        rows = conn.execute(
            f"SELECT control_key FROM concern_controls WHERE concern_id = ? AND control_key IN ({hold_ph})",
            (concern_id, *hold_keys),
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
                if resend_basis is not None:
                    conn.rollback()
                    raise InvalidTransitionError(
                        f"resend evidence is only valid from admitted, got {st!r}",
                        effect_key=effect_key,
                    )
                if _find_unconsumed_retry(recovery) is None:
                    conn.rollback()
                    raise ReconcileRequiredError(
                        "admit blocked: recovery decision required"
                    )
                kind = "decision"
            elif st == STATE_ADMITTED:
                if resend_basis is None:
                    conn.commit()
                    return AdmitResult(
                        effect_key=effect_key,
                        intent_id=intent_id,
                        state=STATE_ADMITTED,
                        replay=False,
                        receipt=None,
                        execution_allowed=False,
                        permit_request_id=None,
                    )
                if not _automatic_retry_allowed(attempts):
                    conn.rollback()
                    raise RetryBudgetExceededError(
                        "automatic retry budget exhausted", effect_key=effect_key
                    )
                kind = "auto_retry"
                assert resend_evidence is not None
                if resend_evidence.kind in ("confirmed_not_accepted", "not_applied"):
                    _bind_non_application(resend_evidence, attempts)
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
            if kind == "auto_retry":
                assert resend_evidence is not None
                attempt_entry["resend_basis"] = resend_basis
                attempt_entry["resend_evidence"] = resend_evidence.to_json()
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
            via = "auto_retry"
            if kind == "decision":
                new_state = STATE_ADMITTED
                via = "decision"
            _set_state(
                conn,
                effect_key=effect_key,
                from_state=st,
                to_state=new_state,
                via=via,
                now_s=now_s,
                columns={
                    "attempt_count": ac,
                    "attempts_json": _dump_json_list(attempts),
                    "recovery_json": _dump_json_list(recovery_new),
                    "fencing_token": fencing_token,
                },
            )
            conn.execute(
                "UPDATE concern_action_intents SET state = ?, updated_at = ? WHERE intent_id = ?",
                (INTENT_OPEN, now_s, intent_id),
            )
            conn.commit()
            return AdmitResult(
                effect_key=effect_key,
                intent_id=intent_id,
                state=new_state,
                replay=False,
                receipt=None,
                execution_allowed=True,
                permit_request_id=request_id,
            )

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
        return AdmitResult(
            effect_key=effect_key,
            intent_id=intent_id,
            state=STATE_ADMITTED,
            replay=False,
            receipt=None,
            execution_allowed=True,
            permit_request_id=request_id,
        )
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
    evidence: EffectEvidence | None = None,
    evidence_provider: EvidenceProvider | None = None,
) -> OutcomeResult:
    """Record terminal or unknown outcome; late reports commit without raising.

    ``failed`` requires verified ``not_applied`` evidence. ``succeeded`` and
    ``outcome_unknown`` reject evidence. A stale-token late report stores no
    evidence. Reconcile of an applied effect without evidence is a separate path.
    """
    now = require_aware_utc(now, name="now")
    if outcome not in (STATE_SUCCEEDED, STATE_FAILED, STATE_OUTCOME_UNKNOWN):
        raise ValueError(f"unknown outcome {outcome!r}")
    if outcome == STATE_FAILED:
        _check_evidence(
            evidence,
            evidence_provider,
            expected_kinds=frozenset({"not_applied"}),
            effect_key=effect_key,
            now=now,
        )
    elif evidence is not None or evidence_provider is not None:
        raise ValueError("evidence is only accepted for outcome 'failed'")
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
                _set_state(
                    conn,
                    effect_key=effect_key,
                    from_state=STATE_ADMITTED,
                    to_state=STATE_OUTCOME_UNKNOWN,
                    via="late_report",
                    now_s=now_s,
                    columns={"attempts_json": _dump_json_list(attempts)},
                )
            else:
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
        if st != STATE_ADMITTED:
            conn.rollback()
            raise InvalidTransitionError(
                f"outcome only from admitted, got {st!r}", effect_key=effect_key
            )

        if outcome == STATE_FAILED:
            assert evidence is not None
            _bind_non_application(evidence, attempts)
            attempts.append(
                {
                    "kind": "outcome_evidence",
                    "outcome": STATE_FAILED,
                    "evidence": evidence.to_json(),
                    "at": now_s,
                }
            )

        receipt_json = (
            json.dumps(receipt, sort_keys=True) if receipt is not None else None
        )
        new_state = outcome
        _set_state(
            conn,
            effect_key=effect_key,
            from_state=STATE_ADMITTED,
            to_state=new_state,
            via="outcome",
            now_s=now_s,
            columns={
                "receipt_json": receipt_json,
                "attempts_json": _dump_json_list(attempts),
            },
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
    evidence: EffectEvidence | None = None,
    evidence_provider: EvidenceProvider | None = None,
) -> None:
    """Reconcile ``outcome_unknown`` to succeeded or reconcile_required_retry.

    ``observed_applied=False`` requires verified ``not_applied`` evidence.
    ``observed_applied=True`` without evidence is allowed and fails closed when
    the row is not ``outcome_unknown``. ``digest`` is opaque to the ledger.
    """
    now = require_aware_utc(now, name="now")
    if not observed_applied:
        _check_evidence(
            evidence,
            evidence_provider,
            expected_kinds=frozenset({"not_applied"}),
            effect_key=effect_key,
            now=now,
        )
    elif evidence is not None or evidence_provider is not None:
        _check_evidence(
            evidence,
            evidence_provider,
            expected_kinds=frozenset({"applied"}),
            effect_key=effect_key,
            now=now,
        )
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
        recovery_row = conn.execute(
            "SELECT recovery_json, attempts_json FROM concern_effects WHERE effect_key = ?",
            (effect_key,),
        ).fetchone()
        attempts = _parse_json_list(recovery_row[1] if recovery_row else None)
        if evidence is not None and evidence.kind == "not_applied":
            _bind_non_application(evidence, attempts)
        recovery = _parse_json_list(recovery_row[0] if recovery_row else None)
        entry: dict[str, Any] = {
            "kind": "reconcile",
            "observed_applied": observed_applied,
            "at": now_s,
        }
        if evidence is not None:
            entry["evidence"] = evidence.to_json()
        recovery.append(entry)
        new_state = (
            STATE_SUCCEEDED if observed_applied else STATE_RECONCILE_REQUIRED_RETRY
        )
        _set_state(
            conn,
            effect_key=effect_key,
            from_state=STATE_OUTCOME_UNKNOWN,
            to_state=new_state,
            via="reconcile",
            now_s=now_s,
            columns={"recovery_json": _dump_json_list(recovery)},
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
    now = require_aware_utc(now, name="now")
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
        if _find_unconsumed_retry(recovery) is not None:
            conn.rollback()
            raise InvalidTransitionError(
                "unconsumed retry decision already exists", effect_key=effect_key
            )
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
    now: datetime,
) -> None:
    """Journal a ``control_set`` hold; rejects unknown *control_key* before journal I/O.

    ``now`` is required and must be aware. This slice does not edit
    ``append_transition``, which stamps the journal row; ``now`` is validated
    and not stored on the hold.
    """
    require_aware_utc(now, name="now")
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
    now: datetime,
) -> None:
    """Journal ``control_clear`` and reopen ``held`` intents when no hold keys remain.

    ``now`` is required and aware. It is stored as ``updated_at`` on reopened
    intents. The journal row timestamp still comes from ``append_transition``.

    A crash after the journal append and before the intent-state update leaves the
    hold cleared in the journal while intents stay ``held`` until a later clear or
    rebuild (A6).
    """
    now = require_aware_utc(now, name="now")
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
                (
                    INTENT_OPEN,
                    _iso_utc(now),
                    concern_id,
                    INTENT_HELD,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
