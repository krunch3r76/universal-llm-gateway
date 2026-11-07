"""Read-only concern work-intent index (Phase 5).

Decisions a:42490 and a:42489: one cortex read boundary enumerates the
Phase-3a ledger and attaches execution observations afterwards. A read
is not a permit. ``blocked`` false is not a launch certificate: an
admitted effect whose owner state is ``running`` or ``completed`` can
still yield ``blocked`` false, and that result does not authorize a launch.

Ledger tables do not advance ``concern_cursor``. ``cortex_digest`` is
the witness for that ledger half (raw stored rows only). Observation
``as_of`` is the caller-declared ``now``, not a clock read inside this
module. Zero ledger intents means the ledger is empty for this concern.

The key that binds an effect to an execution owner is unresolved with
Phase 4 (house 22444). The default key is ``effect_key``. A caller
override that returns ``None`` surfaces as ``unknown``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .canonical_json import CanonicalJsonError, jcs_serialize
from .effects import (
    INTENT_HELD,
    INTENT_OPEN,
    RECOGNIZED_HOLD_KEYS,
    STATE_ADMITTED,
    STATE_FAILED,
    STATE_OUTCOME_UNKNOWN,
    STATE_RECONCILE_REQUIRED_RETRY,
    STATE_SUCCEEDED,
)
from .views import contiguous_through_seq, read_cursor

# Test seam for the interleaved-writer falsifier. Production leaves this None.
statement_boundary_probe: Callable[[str], None] | None = None

OBS_UNAVAILABLE = "unavailable"
OBS_NOT_FOUND = "not_found"
OBS_UNKNOWN = "unknown"
OBS_CONFLICT = "conflict"

_KNOWN_INTENT_STATES = frozenset({INTENT_OPEN, INTENT_HELD})
_KNOWN_EFFECT_STATES = frozenset(
    {
        STATE_ADMITTED,
        STATE_SUCCEEDED,
        STATE_FAILED,
        STATE_OUTCOME_UNKNOWN,
        STATE_RECONCILE_REQUIRED_RETRY,
    }
)
_TERMINAL_CORTEX = frozenset({STATE_SUCCEEDED, STATE_FAILED})
_BLOCKING_CORTEX_STATES = frozenset(
    {STATE_ADMITTED, STATE_OUTCOME_UNKNOWN, STATE_RECONCILE_REQUIRED_RETRY}
)
_OBS_BLOCKING = frozenset({OBS_UNAVAILABLE, OBS_UNKNOWN, OBS_CONFLICT})

IdentityKeyFn = Callable[[dict[str, Any]], str | None]


class ExecutionOwnerPort(Protocol):
    """Structural port for a Phase-4 owner lookup. Read-only."""

    def health(self) -> bool: ...

    def lookup(self, identity_key: str) -> Any: ...


@dataclass(frozen=True)
class IntentIndex:
    """One concern's ledger index plus derived observations."""

    concern_id: str
    through_seq: int
    cursor_token: str
    cortex_digest: str
    cortex_half: dict[str, Any]
    derived: dict[str, Any]
    completeness: str
    completeness_scope: str
    blocked: bool
    blocked_reasons: tuple[dict[str, Any], ...]
    integrity_errors: tuple[dict[str, Any], ...]
    owner_consulted: bool
    journal_intents: tuple[str, ...]
    journal_intents_count: int
    intents: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready copy. Keys never include permit language."""
        return {
            "concern_id": self.concern_id,
            "through_seq": self.through_seq,
            "cursor_token": self.cursor_token,
            "cortex_digest": self.cortex_digest,
            "cortex_half": self.cortex_half,
            "derived": self.derived,
            "completeness": self.completeness,
            "completeness_scope": self.completeness_scope,
            "blocked": self.blocked,
            "blocked_reasons": list(self.blocked_reasons),
            "integrity_errors": list(self.integrity_errors),
            "owner_consulted": self.owner_consulted,
            "journal_intents": list(self.journal_intents),
            "journal_intents_count": self.journal_intents_count,
            "intents": list(self.intents),
        }


def read_intent_index(
    conn: sqlite3.Connection,
    concern_id: str,
    *,
    now: datetime,
    owner: ExecutionOwnerPort | None = None,
    identity_key_for: IdentityKeyFn | None = None,
    owner_label: str | None = None,
) -> IntentIndex:
    """Enumerate one concern's work intents inside a single read snapshot.

    Raises ``ValueError`` when ``now`` is naive or ``conn`` is already in
    a transaction. The check runs before ``BEGIN``. A Python 3.12
    connection opened with ``autocommit=False`` always raises
    ``ValueError`` here, because that mode keeps a transaction open.
    The connection's ``query_only`` pragma is set for the ledger snapshot
    and restored before the owner join, so ``identity_key_for`` and
    ``owner.lookup`` do not run under ``query_only``.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if conn.in_transaction:
        raise ValueError("connection is already in a transaction")

    prior = conn.execute("PRAGMA query_only").fetchone()
    prior_on = int(prior[0]) if prior is not None else 0
    conn.execute("PRAGMA query_only=ON")
    snapshot_done = False
    try:
        result = _read_snapshot(
            conn,
            concern_id,
            now=now,
            owner=owner,
            identity_key_for=identity_key_for,
            owner_label=owner_label,
            query_only_prior=prior_on,
        )
        snapshot_done = True
        return result
    finally:
        try:
            if conn.in_transaction and not snapshot_done:
                conn.rollback()
        finally:
            conn.execute(f"PRAGMA query_only={prior_on}")


def _probe(label: str) -> None:
    probe = statement_boundary_probe
    if probe is not None:
        probe(label)


def _parse_expiry(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _read_snapshot(
    conn: sqlite3.Connection,
    concern_id: str,
    *,
    now: datetime,
    owner: ExecutionOwnerPort | None,
    identity_key_for: IdentityKeyFn | None,
    owner_label: str | None,
    query_only_prior: int,
) -> IntentIndex:
    integrity: list[dict[str, Any]] = []
    conn.execute("BEGIN DEFERRED")
    try:
        through_seq, cursor = read_cursor(conn, concern_id)
        _probe("after_cursor")
        cursor_token = str(cursor)
        journal_through = contiguous_through_seq(conn, concern_id)
        if journal_through != through_seq:
            integrity.append(
                {
                    "kind": "view_lag",
                    "effect_key": None,
                    "intent_id": None,
                    "work_key": None,
                    "detail": f"journal_through_seq={journal_through} cursor={through_seq}",
                }
            )

        journal_ids = [
            str(row[0])
            for row in conn.execute(
                "SELECT intent_id FROM concern_intents WHERE concern_id = ? ORDER BY intent_id",
                (concern_id,),
            ).fetchall()
        ]
        intent_rows = [
            {
                "concern_id": str(r[0]),
                "work_key": str(r[1]),
                "action_slot": str(r[2]),
                "run_generation": int(r[3]),
                "intent_id": str(r[4]),
                "state": str(r[5]),
                "created_at": str(r[6]),
                "updated_at": str(r[7]),
            }
            for r in conn.execute(
                "SELECT concern_id, work_key, action_slot, run_generation, intent_id, "
                "state, created_at, updated_at FROM concern_action_intents "
                "WHERE concern_id = ? "
                "ORDER BY work_key, action_slot, run_generation, intent_id",
                (concern_id,),
            ).fetchall()
        ]
        effect_rows = [
            {
                "effect_key": str(r[0]),
                "concern_id": str(r[1]),
                "work_key": str(r[2]),
                "intent_id": str(r[3]),
                "effect_kind": str(r[4]),
                "material_fingerprint": str(r[5]),
                "fencing_token": int(r[6]),
                "state": str(r[7]),
                "attempt_count": int(r[8]),
                "attempts_json": str(r[9]),
                "receipt_json": None if r[10] is None else str(r[10]),
                "recovery_json": str(r[11]),
                "created_at": str(r[12]),
                "updated_at": str(r[13]),
            }
            for r in conn.execute(
                "SELECT effect_key, concern_id, work_key, intent_id, effect_kind, "
                "material_fingerprint, fencing_token, state, attempt_count, attempts_json, "
                "receipt_json, recovery_json, created_at, updated_at "
                "FROM concern_effects WHERE concern_id = ? OR intent_id IN ("
                "SELECT intent_id FROM concern_action_intents WHERE concern_id = ?) "
                "ORDER BY effect_key",
                (concern_id, concern_id),
            ).fetchall()
        ]
        lease_rows = [
            {
                "concern_id": str(r[0]),
                "work_key": str(r[1]),
                "holder": str(r[2]),
                "owner_generation": int(r[3]),
                "lease_expires_at": str(r[4]),
            }
            for r in conn.execute(
                "SELECT concern_id, work_key, holder, owner_generation, lease_expires_at "
                "FROM concern_effect_leases WHERE concern_id = ? ORDER BY work_key",
                (concern_id,),
            ).fetchall()
        ]
        hold_keys = [
            str(r[0])
            for r in conn.execute(
                "SELECT control_key FROM concern_controls WHERE concern_id = ? ORDER BY control_key",
                (concern_id,),
            ).fetchall()
            if str(r[0]) in RECOGNIZED_HOLD_KEYS
        ]
    finally:
        conn.rollback()

    # Owner identity and lookup may write. They run after the snapshot ends.
    conn.execute(f"PRAGMA query_only={query_only_prior}")

    by_intent = {row["intent_id"]: row for row in intent_rows}
    intent_work = {row["intent_id"]: row["work_key"] for row in intent_rows}
    intent_ids = set(by_intent)

    for row in intent_rows:
        if row["state"] not in _KNOWN_INTENT_STATES:
            integrity.append(
                {
                    "kind": "unknown_state",
                    "effect_key": None,
                    "intent_id": row["intent_id"],
                    "work_key": row["work_key"],
                }
            )

    decoded_effects: list[dict[str, Any]] = []
    for eff in effect_rows:
        receipt, receipt_ok = _decode_json_field(eff["receipt_json"])
        recovery, recovery_ok = _decode_json_field(eff["recovery_json"])
        if not receipt_ok or not recovery_ok:
            integrity.append(
                {
                    "kind": "malformed_json",
                    "effect_key": eff["effect_key"],
                    "intent_id": eff["intent_id"],
                    "work_key": eff["work_key"],
                }
            )
        if eff["intent_id"] not in intent_ids:
            integrity.append(
                {
                    "kind": "orphan_effect",
                    "effect_key": eff["effect_key"],
                    "intent_id": eff["intent_id"],
                    "work_key": eff["work_key"],
                }
            )
        elif intent_work[eff["intent_id"]] != eff["work_key"]:
            integrity.append(
                {
                    "kind": "work_key_mismatch",
                    "effect_key": eff["effect_key"],
                    "intent_id": eff["intent_id"],
                    "work_key": eff["work_key"],
                }
            )
        if eff["concern_id"] != concern_id:
            integrity.append(
                {
                    "kind": "concern_mismatch",
                    "effect_key": eff["effect_key"],
                    "intent_id": eff["intent_id"],
                    "work_key": eff["work_key"],
                }
            )
        if eff["state"] not in _KNOWN_EFFECT_STATES:
            integrity.append(
                {
                    "kind": "unknown_state",
                    "effect_key": eff["effect_key"],
                    "intent_id": eff["intent_id"],
                    "work_key": eff["work_key"],
                }
            )
        decoded_effects.append({**eff, "receipt": receipt, "recovery": recovery})

    leases_by_work = {row["work_key"]: row for row in lease_rows}
    works_with_intent = {row["work_key"] for row in intent_rows}
    derived_leases: list[dict[str, Any]] = []
    for lease in lease_rows:
        live, parsed = _lease_live(lease["lease_expires_at"], now)
        item = {**lease, "lease_live": live}
        derived_leases.append(item)
        if not parsed:
            integrity.append(
                {
                    "kind": "unparseable_lease_expiry",
                    "effect_key": None,
                    "intent_id": None,
                    "work_key": lease["work_key"],
                }
            )
        if lease["work_key"] not in works_with_intent:
            integrity.append(
                {
                    "kind": "lease_without_intent",
                    "effect_key": None,
                    "intent_id": None,
                    "work_key": lease["work_key"],
                }
            )

    cortex_half = {
        "cursor": {"through_seq": through_seq, "cursor_token": cursor_token},
        "journal_intent_ids": journal_ids,
        "journal_through_seq": journal_through,
        "intents": intent_rows,
        "effects": effect_rows,
        "leases": lease_rows,
        "holds": hold_keys,
    }
    digest = ""
    try:
        digest = hashlib.sha256(jcs_serialize(cortex_half).encode("utf-8")).hexdigest()
    except CanonicalJsonError:
        integrity.append(
            {
                "kind": "non_canonical",
                "effect_key": None,
                "intent_id": None,
                "work_key": None,
            }
        )

    observations, owner_consulted = _observe(
        decoded_effects,
        owner=owner,
        identity_key_for=identity_key_for,
        owner_label=owner_label,
        now=now,
    )
    obs_by_key = {item["effect_key"]: item for item in observations}

    derived_effects: list[dict[str, Any]] = []
    for eff in decoded_effects:
        lease = leases_by_work.get(eff["work_key"])
        claim = _claim_status(eff, lease, now, integrity)
        derived_effects.append(
            {
                "effect_key": eff["effect_key"],
                "intent_id": eff["intent_id"],
                "work_key": eff["work_key"],
                "state": eff["state"],
                "fencing_token": eff["fencing_token"],
                "claim_status": claim,
                "receipt": eff["receipt"],
                "recovery": eff["recovery"],
                "observation": obs_by_key.get(eff["effect_key"]),
            }
        )

    reasons = _blocked_reasons(
        integrity=integrity,
        effects=derived_effects,
    )
    completeness = "incomplete" if integrity else "complete"
    blocked = bool(reasons) or completeness == "incomplete"

    effects_by_intent: dict[str, list[dict[str, Any]]] = {}
    for eff in derived_effects:
        effects_by_intent.setdefault(eff["intent_id"], []).append(eff)
    for bucket in effects_by_intent.values():
        bucket.sort(key=lambda item: item["effect_key"])

    intents_out: list[dict[str, Any]] = []
    for row in intent_rows:
        intents_out.append(
            {
                "intent_id": row["intent_id"],
                "work_key": row["work_key"],
                "action_slot": row["action_slot"],
                "run_generation": row["run_generation"],
                "state": row["state"],
                "effects": effects_by_intent.get(row["intent_id"], []),
            }
        )

    derived = {
        "leases": derived_leases,
        "effects": derived_effects,
        "observations": observations,
    }
    return IntentIndex(
        concern_id=concern_id,
        through_seq=through_seq,
        cursor_token=cursor_token,
        cortex_digest=digest,
        cortex_half=cortex_half,
        derived=derived,
        completeness=completeness,
        completeness_scope="ledger",
        blocked=blocked,
        blocked_reasons=tuple(reasons),
        integrity_errors=tuple(integrity),
        owner_consulted=owner_consulted,
        journal_intents=tuple(journal_ids),
        journal_intents_count=len(journal_ids),
        intents=tuple(intents_out),
    )


def _decode_json_field(raw: str | None) -> tuple[Any, bool]:
    if raw is None:
        return None, True
    try:
        return json.loads(raw), True
    except json.JSONDecodeError:
        return None, False


def _lease_live(raw: str, now: datetime) -> tuple[bool, bool]:
    try:
        exp = _parse_expiry(raw)
    except ValueError:
        return False, False
    return now.astimezone(UTC) <= exp, True


def _claim_status(
    eff: dict[str, Any],
    lease: dict[str, Any] | None,
    now: datetime,
    integrity: list[dict[str, Any]],
) -> str:
    """Classify one effect's lease.

    Returned values are exactly ``no_lease``, ``unparseable_lease_expiry``,
    ``fencing_token_ahead``, ``stale``, ``live``, and ``expired``.
    """
    if lease is None:
        claim = "no_lease"
    else:
        live, parsed = _lease_live(lease["lease_expires_at"], now)
        gen = int(lease["owner_generation"])
        token = int(eff["fencing_token"])
        if not parsed:
            claim = "unparseable_lease_expiry"
        elif token > gen:
            claim = "fencing_token_ahead"
            integrity.append(
                {
                    "kind": "fencing_token_ahead",
                    "effect_key": eff["effect_key"],
                    "intent_id": eff["intent_id"],
                    "work_key": eff["work_key"],
                }
            )
        elif token < gen:
            claim = "stale"
        elif live:
            claim = "live"
        else:
            claim = "expired"
    if eff["state"] == STATE_ADMITTED and claim in {"stale", "no_lease"}:
        integrity.append(
            {
                "kind": "admitted_stale_or_no_lease",
                "effect_key": eff["effect_key"],
                "intent_id": eff["intent_id"],
                "work_key": eff["work_key"],
            }
        )
    return claim


def _has_retry(recovery: Any) -> bool:
    """True when recovery holds a retry decision that is not consumed.

    Matches ``effects._find_unconsumed_retry``: a retry entry with
    ``consumed_attempt`` set is not outstanding.
    """
    if not isinstance(recovery, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("decision") == "retry"
        and not item.get("consumed_attempt")
        for item in recovery
    )


def _blocked_reasons(
    *,
    integrity: list[dict[str, Any]],
    effects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    for err in integrity:
        reasons.append(
            {
                "cause": err["kind"],
                "effect_key": err.get("effect_key"),
                "intent_id": err.get("intent_id"),
                "work_key": err.get("work_key"),
            }
        )
    for eff in effects:
        obs = eff.get("observation") or {}
        obs_state = obs.get("state")
        cortex = eff["state"]
        base = {
            "effect_key": eff["effect_key"],
            "intent_id": eff["intent_id"],
            "work_key": eff["work_key"],
        }
        if cortex in {STATE_OUTCOME_UNKNOWN, STATE_RECONCILE_REQUIRED_RETRY}:
            reasons.append({**base, "cause": cortex})
        if cortex in _BLOCKING_CORTEX_STATES and obs_state in _OBS_BLOCKING:
            reasons.append({**base, "cause": obs_state})
        if cortex == STATE_ADMITTED and obs_state == OBS_NOT_FOUND:
            reasons.append({**base, "cause": OBS_NOT_FOUND})
        if cortex == STATE_FAILED and obs_state == OBS_CONFLICT:
            reasons.append({**base, "cause": "failed_conflict"})
        if (
            cortex == STATE_FAILED
            and obs_state in {OBS_UNAVAILABLE, OBS_UNKNOWN}
            and _has_retry(eff.get("recovery"))
        ):
            reasons.append({**base, "cause": "failed_retry_unresolved"})
    return reasons


def _state_str(value: Any) -> str | None:
    """Return a plain ``str``. ``StrEnum`` members are ``str`` subclasses."""
    if value is None:
        return None
    if type(value) is str:
        return value
    if isinstance(value, str):
        return str(value)
    inner = getattr(value, "value", None)
    if isinstance(inner, str):
        return inner
    return str(value)


def _conflict(cortex_state: str, owner_state: str) -> bool:
    if cortex_state not in _TERMINAL_CORTEX:
        return False
    if cortex_state == STATE_SUCCEEDED:
        return owner_state != "completed"
    return owner_state not in {"failed", "cancelled"}


def _observe(
    effects: list[dict[str, Any]],
    *,
    owner: ExecutionOwnerPort | None,
    identity_key_for: IdentityKeyFn | None,
    owner_label: str | None,
    now: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    if not effects:
        return [], False
    source = owner_label or (type(owner).__name__ if owner is not None else "none")
    as_of = now.isoformat()
    basis = "caller_supplied" if identity_key_for is not None else "effect_key_default"
    key_fn = identity_key_for or (lambda eff: str(eff["effect_key"]))

    if owner is None:
        return [
            _obs(
                eff,
                state=OBS_UNAVAILABLE,
                source=source,
                as_of=as_of,
                basis=basis,
                identity_key=None,
                reason="owner_missing",
            )
            for eff in effects
        ], False

    try:
        healthy = bool(owner.health())
    except Exception as exc:
        return [
            _obs(
                eff,
                state=OBS_UNAVAILABLE,
                source=source,
                as_of=as_of,
                basis=basis,
                identity_key=None,
                reason=f"health_error:{type(exc).__name__}",
            )
            for eff in effects
        ], True
    if not healthy:
        return [
            _obs(
                eff,
                state=OBS_UNAVAILABLE,
                source=source,
                as_of=as_of,
                basis=basis,
                identity_key=None,
                reason="health_false",
            )
            for eff in effects
        ], True

    out: list[dict[str, Any]] = []
    for eff in effects:
        try:
            identity_key = key_fn(eff)
        except Exception as exc:
            out.append(
                _obs(
                    eff,
                    state=OBS_UNKNOWN,
                    source=source,
                    as_of=as_of,
                    basis=basis,
                    identity_key=None,
                    reason=f"identity_key_error:{type(exc).__name__}",
                )
            )
            continue
        if identity_key is None:
            out.append(
                _obs(
                    eff,
                    state=OBS_UNKNOWN,
                    source=source,
                    as_of=as_of,
                    basis=basis,
                    identity_key=None,
                    reason="no_identity_key",
                )
            )
            continue
        try:
            found = owner.lookup(str(identity_key))
        except Exception as exc:
            out.append(
                _obs(
                    eff,
                    state=OBS_UNAVAILABLE,
                    source=source,
                    as_of=as_of,
                    basis=basis,
                    identity_key=str(identity_key),
                    reason=f"lookup_error:{type(exc).__name__}",
                )
            )
            continue
        out.append(
            _classify(
                eff,
                found,
                identity_key=str(identity_key),
                source=source,
                as_of=as_of,
                basis=basis,
            )
        )
    return out, True


def _obs(
    eff: dict[str, Any],
    *,
    state: str,
    source: str,
    as_of: str,
    basis: str,
    identity_key: str | None,
    reason: str | None = None,
    store_epoch: str | None = None,
    epoch_status: str | None = None,
) -> dict[str, Any]:
    return {
        "effect_key": eff["effect_key"],
        "state": state,
        "identity_key": identity_key,
        "identity_key_basis": basis,
        "store_epoch": store_epoch,
        "epoch_status": epoch_status,
        "source": source,
        "as_of": as_of,
        "reason": reason,
    }


def _classify(
    eff: dict[str, Any],
    found: Any,
    *,
    identity_key: str,
    source: str,
    as_of: str,
    basis: str,
) -> dict[str, Any]:
    epoch = _state_str(getattr(found, "epoch_status", None)) or ""
    store_epoch = _state_str(getattr(found, "store_epoch", None))
    common = dict(
        identity_key=identity_key,
        source=source,
        as_of=as_of,
        basis=basis,
        store_epoch=store_epoch,
        epoch_status=epoch or None,
    )
    if epoch == OBS_UNAVAILABLE:
        return _obs(eff, state=OBS_UNAVAILABLE, reason="epoch_unavailable", **common)
    if epoch != "ok":
        return _obs(
            eff, state=OBS_UNKNOWN, reason=f"epoch_{epoch or 'missing'}", **common
        )
    if not bool(getattr(found, "found", False)):
        return _obs(eff, state=OBS_NOT_FOUND, reason="not_found", **common)
    owner_state = _state_str(getattr(found, "state", None))
    if owner_state in {None, "outcome_unknown", "blocked_unknown"}:
        return _obs(eff, state=OBS_UNKNOWN, reason="owner_state_unknown", **common)
    if _conflict(eff["state"], owner_state):
        return _obs(eff, state=OBS_CONFLICT, reason="terminal_disagreement", **common)
    return _obs(eff, state=owner_state, reason=None, **common)
