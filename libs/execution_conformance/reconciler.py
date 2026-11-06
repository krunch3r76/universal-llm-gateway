"""Reconcile local intent ledger state against actuator owner lookup."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .adapters import ActuatorAdapter, OwnerLookup, validate_actuator_identity_key
from .ledger import (
    STICKY_BLOCKED_REASONS,
    IntentRecord,
    IntentStore,
    ReferenceSqliteIntentLedger,
)
from .manifest import ActuatorName
from .states import (
    TERMINAL_STATES,
    ExecutionState,
    IllegalStateTransitionError,
    adoption_target,
)

COMPENSATION_ALLOWLIST = frozenset({"conformance-reviewer", "reviewer"})

TERMINAL_LOCAL: frozenset[ExecutionState] = TERMINAL_STATES

IN_FLIGHT: frozenset[ExecutionState] = frozenset(
    {ExecutionState.reserved, ExecutionState.accepted, ExecutionState.running}
)

DEFAULT_CLAIM_TTL_SECONDS = 30.0
DEFAULT_UNAVAILABLE_MAX_AGE_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class CompensatingAction:
    """Authorized human/agent action required to exit ``blocked_unknown`` via compensation."""

    authorized_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class DispatchResult:
    intent_id: str
    state: ExecutionState
    actuator_ref: str | None
    redispatched: bool = False
    adopted_existing: bool = False
    transient: bool = False


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    intent_id: str
    state: ExecutionState
    adopted: bool
    redispatched: bool
    blocked: bool = False
    transient: bool = False
    adopted_existing: bool = False
    epoch_unavailable_stale: bool = False


def payload_admit_kwargs(record: IntentRecord) -> dict[str, Any]:
    if not record.payload:
        return {}
    raw = record.payload.get("admit_kwargs")
    return dict(raw) if isinstance(raw, dict) else {}


def dispatch_intent(
    ledger: IntentStore,
    adapter: ActuatorAdapter,
    *,
    intent_id: str,
    identity_key: str,
    admit_kwargs: dict[str, Any] | None = None,
    clock: Callable[[], float] | None = None,
    claim_ttl_seconds: float = DEFAULT_CLAIM_TTL_SECONDS,
) -> DispatchResult:
    """Reserve locally; admit only on ``reserve().created`` with owner miss."""
    clock_fn = clock or time.time
    validate_actuator_identity_key(adapter, identity_key)
    lookup_pre = adapter.lookup(identity_key)
    if lookup_pre.epoch_status == "unavailable":
        return DispatchResult(
            intent_id=intent_id,
            state=ExecutionState.reserved,
            actuator_ref=None,
            transient=True,
        )
    payload = {"admit_kwargs": admit_kwargs or {}}
    store_epoch, payload = _store_epoch_for_reserve(lookup_pre, adapter, payload)
    actuator: ActuatorName = adapter.name  # type: ignore[assignment]
    reserved = ledger.reserve(
        intent_id=intent_id,
        actuator=actuator,
        identity_key=identity_key,
        store_epoch=store_epoch,
        payload=payload,
        inflight_claimed_at=clock_fn(),
    )
    record = reserved.record
    if not reserved.created:
        if record.intent_id != intent_id:
            return DispatchResult(
                intent_id=intent_id,
                state=record.state,
                actuator_ref=record.actuator_ref,
                adopted_existing=True,
            )
        rec = reconcile(
            ledger,
            adapter,
            intent_id,
            clock=clock_fn,
            claim_ttl_seconds=claim_ttl_seconds,
        )
        return _reconcile_as_dispatch(ledger, rec)

    lookup = adapter.lookup(identity_key)
    if _lookup_epoch_unavailable(lookup):
        return DispatchResult(
            intent_id=intent_id,
            state=record.state,
            actuator_ref=record.actuator_ref,
            transient=True,
        )

    _capture_store_epoch_if_pending(ledger, intent_id, lookup)
    _clear_epoch_unavailable_if_ok(ledger, intent_id, lookup)

    adopted = _try_adopt_from_lookup(ledger, intent_id, record, lookup)
    if adopted is not None:
        return adopted

    try:
        outcome = adapter.admit(identity_key, **payload_admit_kwargs(record))
    except Exception as exc:
        adopted = _recover_after_admit_error(
            ledger, adapter, intent_id, identity_key, record
        )
        if adopted is not None:
            return adopted
        _block_unknown(ledger, intent_id, reason="admit_error")
        raise exc

    try:
        row = ledger.record_receipt(
            intent_id,
            actuator_ref=outcome.actuator_ref,
            state=outcome.state,
        )
    except IllegalStateTransitionError:
        rec = reconcile(
            ledger,
            adapter,
            intent_id,
            clock=clock_fn,
            claim_ttl_seconds=claim_ttl_seconds,
        )
        return _reconcile_as_dispatch(ledger, rec)
    return DispatchResult(
        intent_id=intent_id,
        state=row.state,
        actuator_ref=row.actuator_ref,
    )


def reconcile(
    ledger: IntentStore,
    adapter: ActuatorAdapter,
    intent_id: str,
    *,
    clock: Callable[[], float] | None = None,
    claim_ttl_seconds: float = DEFAULT_CLAIM_TTL_SECONDS,
    unavailable_max_age_seconds: float = DEFAULT_UNAVAILABLE_MAX_AGE_SECONDS,
) -> ReconcileResult:
    """Drive local intent row toward owner truth without duplicate remote admits."""
    clock_fn = clock or time.time
    record = ledger.get(intent_id)
    if record is None:
        raise KeyError(f"unknown intent {intent_id!r}")

    if record.state in TERMINAL_LOCAL:
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            redispatched=False,
            blocked=False,
        )

    if record.state == ExecutionState.blocked_unknown:
        return _reconcile_blocked_unknown(
            ledger,
            adapter,
            intent_id,
            record,
            clock_fn=clock_fn,
            unavailable_max_age_seconds=unavailable_max_age_seconds,
        )

    lookup = adapter.lookup(record.identity_key)
    unavailable = _epoch_unavailable_outcome(
        ledger,
        intent_id,
        record,
        lookup,
        clock_fn,
        unavailable_max_age_seconds,
    )
    if unavailable is not None:
        return unavailable

    _capture_store_epoch_if_pending(ledger, intent_id, lookup)
    _clear_epoch_unavailable_if_ok(ledger, intent_id, lookup)

    adopted = _try_adopt_from_lookup(ledger, intent_id, record, lookup)
    if adopted is not None:
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        blocked = row.state == ExecutionState.blocked_unknown
        return ReconcileResult(
            intent_id=intent_id,
            state=row.state,
            adopted=not blocked,
            adopted_existing=not blocked,
            redispatched=False,
            blocked=blocked,
        )

    if (
        record.state in (ExecutionState.reserved, ExecutionState.outcome_unknown)
        and adapter.admit_idempotent
        and _retry_epoch_allows(record, lookup, adapter)
    ):
        if ledger.claim_retry(intent_id, inflight_at=clock_fn()):
            kwargs = payload_admit_kwargs(record)
            try:
                outcome = adapter.admit(record.identity_key, **kwargs)
            except Exception:
                adopted = _recover_after_admit_error(
                    ledger,
                    adapter,
                    intent_id,
                    record.identity_key,
                    record,
                )
                if adopted is not None:
                    row = ledger.get(intent_id)
                    if row is None:
                        raise KeyError(intent_id)
                    return ReconcileResult(
                        intent_id=intent_id,
                        state=row.state,
                        adopted=True,
                        redispatched=False,
                        blocked=False,
                    )
                _block_unknown(ledger, intent_id, reason="admit_error")
                row = ledger.get(intent_id)
                if row is None:
                    raise KeyError(intent_id)
                return ReconcileResult(
                    intent_id=intent_id,
                    state=row.state,
                    adopted=False,
                    redispatched=False,
                    blocked=True,
                )
            try:
                row = ledger.record_receipt(
                    intent_id,
                    actuator_ref=outcome.actuator_ref,
                    state=outcome.state,
                )
            except IllegalStateTransitionError:
                return _result_after_lost_claim(
                    ledger, intent_id, clock_fn, claim_ttl_seconds
                )
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=False,
                redispatched=True,
            )
        return _result_after_lost_claim(
            ledger, intent_id, clock_fn, claim_ttl_seconds
        )

    if _inflight_lease_active(record, clock_fn, claim_ttl_seconds):
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            redispatched=False,
            blocked=False,
            transient=True,
        )

    unavailable = _epoch_unavailable_outcome(
        ledger,
        intent_id,
        record,
        lookup,
        clock_fn,
        unavailable_max_age_seconds,
    )
    if unavailable is not None:
        return unavailable

    if record.inflight_claimed_at is not None and (
        clock_fn() - record.inflight_claimed_at
    ) >= claim_ttl_seconds:
        reason = "retry_claim_stale"
    else:
        reason = _miss_block_reason(record, lookup)
    _block_unknown(ledger, intent_id, reason=reason)
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    return ReconcileResult(
        intent_id=intent_id,
        state=row.state,
        adopted=False,
        adopted_existing=False,
        redispatched=False,
        blocked=True,
    )


def _epoch_unavailable_outcome(
    ledger: IntentStore,
    intent_id: str,
    record: IntentRecord,
    lookup: OwnerLookup,
    clock_fn: Callable[[], float],
    unavailable_max_age_seconds: float,
) -> ReconcileResult | None:
    if lookup.epoch_status != "unavailable":
        return None
    payload = record.payload or {}
    since_raw = payload.get("epoch_unavailable_since")
    now = clock_fn()
    if since_raw is None:
        ledger.merge_payload(intent_id, {"epoch_unavailable_since": now})
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            adopted_existing=False,
            redispatched=False,
            blocked=False,
            transient=True,
        )
    since = float(since_raw)
    if (now - since) >= unavailable_max_age_seconds:
        _block_unknown(ledger, intent_id, reason="epoch_unavailable_stale")
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return ReconcileResult(
            intent_id=intent_id,
            state=row.state,
            adopted=False,
            adopted_existing=False,
            redispatched=False,
            blocked=True,
            epoch_unavailable_stale=True,
        )
    return ReconcileResult(
        intent_id=intent_id,
        state=record.state,
        adopted=False,
        adopted_existing=False,
        redispatched=False,
        blocked=False,
        transient=True,
    )


def _inflight_lease_active(
    record: IntentRecord,
    clock_fn: Callable[[], float],
    claim_ttl_seconds: float,
) -> bool:
    if record.inflight_claimed_at is None:
        return False
    if record.state != ExecutionState.reserved:
        return False
    return (clock_fn() - record.inflight_claimed_at) < claim_ttl_seconds


def _result_after_lost_claim(
    ledger: IntentStore,
    intent_id: str,
    clock_fn: Callable[[], float],
    claim_ttl_seconds: float,
) -> ReconcileResult:
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    if row.state not in (
        ExecutionState.reserved,
        ExecutionState.outcome_unknown,
    ):
        return ReconcileResult(
            intent_id=intent_id,
            state=row.state,
            adopted=row.state not in (ExecutionState.blocked_unknown,),
            redispatched=False,
            blocked=row.state == ExecutionState.blocked_unknown,
        )
    if row.inflight_claimed_at is not None and (
        clock_fn() - row.inflight_claimed_at
    ) < claim_ttl_seconds:
        return ReconcileResult(
            intent_id=intent_id,
            state=row.state,
            adopted=False,
            redispatched=False,
            blocked=False,
            transient=True,
        )
    reason = (
        "retry_claim_stale"
        if row.inflight_claimed_at is not None
        else "owner_miss"
    )
    _block_unknown(ledger, intent_id, reason=reason)
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    return ReconcileResult(
        intent_id=intent_id,
        state=row.state,
        adopted=False,
        redispatched=False,
        blocked=True,
    )


def _recover_after_admit_error(
    ledger: IntentStore,
    adapter: ActuatorAdapter,
    intent_id: str,
    identity_key: str,
    record: IntentRecord,
) -> DispatchResult | None:
    lookup = adapter.lookup(identity_key)
    if (
        lookup.found
        and lookup.state is not None
        and lookup.state != ExecutionState.outcome_unknown
    ):
        return _try_adopt_from_lookup(ledger, intent_id, record, lookup)
    return None


def _miss_block_reason(record: IntentRecord, lookup: OwnerLookup) -> str:
    if lookup.epoch_status == "unprovisioned" or not record.store_epoch:
        return "epoch_unverified"
    if lookup.epoch_status == "ok" and record.store_epoch != (lookup.store_epoch or ""):
        return "epoch_mismatch"
    return "owner_miss"


def _reconcile_blocked_unknown(
    ledger: IntentStore,
    adapter: ActuatorAdapter,
    intent_id: str,
    record: IntentRecord,
    *,
    clock_fn: Callable[[], float],
    unavailable_max_age_seconds: float,
) -> ReconcileResult:
    payload = record.payload or {}
    blocked_reason = payload.get("blocked_reason")
    if blocked_reason in ("ref_conflict", "duplicate_msg"):
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            adopted_existing=False,
            redispatched=False,
            blocked=True,
        )

    lookup = adapter.lookup(record.identity_key)
    unavailable = _epoch_unavailable_outcome(
        ledger,
        intent_id,
        record,
        lookup,
        clock_fn,
        unavailable_max_age_seconds,
    )
    if unavailable is not None:
        if unavailable.blocked:
            return ReconcileResult(
                intent_id=unavailable.intent_id,
                state=unavailable.state,
                adopted=False,
                adopted_existing=False,
                redispatched=False,
                blocked=True,
                epoch_unavailable_stale=unavailable.epoch_unavailable_stale,
            )
        return ReconcileResult(
            intent_id=unavailable.intent_id,
            state=unavailable.state,
            adopted=False,
            adopted_existing=False,
            redispatched=False,
            blocked=True,
            transient=True,
        )

    if not adapter.admit_idempotent and blocked_reason in (
        "epoch_mismatch",
        "epoch_unverified",
    ):
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            adopted_existing=False,
            redispatched=False,
            blocked=True,
        )
    if (
        not adapter.admit_idempotent
        and record.store_epoch
        and lookup.store_epoch
        and record.store_epoch != lookup.store_epoch
    ):
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            adopted_existing=False,
            redispatched=False,
            blocked=True,
        )

    adopted = _adopt_blocked_from_lookup(ledger, intent_id, record, lookup)
    if adopted is not None:
        return adopted

    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    return ReconcileResult(
        intent_id=intent_id,
        state=row.state,
        adopted=False,
        redispatched=False,
        blocked=True,
    )


def _adopt_blocked_from_lookup(
    ledger: IntentStore,
    intent_id: str,
    record: IntentRecord,
    lookup: OwnerLookup,
) -> ReconcileResult | None:
    if not lookup.found or lookup.state in (
        None,
        ExecutionState.outcome_unknown,
    ):
        return None

    if record.actuator_ref and lookup.actuator_ref:
        if record.actuator_ref != lookup.actuator_ref:
            _block_unknown(ledger, intent_id, reason="ref_conflict")
            row = ledger.get(intent_id)
            if row is None:
                raise KeyError(intent_id)
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=False,
                redispatched=False,
                blocked=True,
            )

    owner_state = lookup.state or ExecutionState.accepted
    payload = record.payload or {}
    pre = payload.get("pre_block_state", ExecutionState.reserved.value)
    baseline = ExecutionState(pre)
    target = adoption_target(baseline, owner_state)
    ref = lookup.actuator_ref or record.actuator_ref
    row = ledger.promote_blocked_adoption(
        intent_id,
        target=target,
        actuator_ref=ref,
        owner_unknown_reason=lookup.unknown_reason,
    )
    if row is None:
        fresh = ledger.get(intent_id)
        if fresh is None:
            raise KeyError(intent_id)
        return ReconcileResult(
            intent_id=intent_id,
            state=fresh.state,
            adopted=fresh.state not in (ExecutionState.blocked_unknown,),
            adopted_existing=fresh.state not in (ExecutionState.blocked_unknown,),
            redispatched=False,
            blocked=fresh.state == ExecutionState.blocked_unknown,
        )
    return ReconcileResult(
        intent_id=intent_id,
        state=row.state,
        adopted=True,
        adopted_existing=True,
        redispatched=False,
        blocked=False,
    )


def _try_adopt_from_lookup(
    ledger: IntentStore,
    intent_id: str,
    record: IntentRecord,
    lookup: OwnerLookup,
) -> DispatchResult | None:
    if not lookup.found:
        return None

    _clear_epoch_unavailable_if_ok(ledger, intent_id, lookup)

    if lookup.state is None or lookup.state == ExecutionState.outcome_unknown:
        reason = (
            "duplicate_msg"
            if lookup.unknown_reason == "duplicate_msg"
            else "owner_state_unknown"
        )
        _block_unknown(ledger, intent_id, reason=reason)
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return DispatchResult(
            intent_id=intent_id,
            state=row.state,
            actuator_ref=row.actuator_ref,
        )

    if lookup.unknown_reason:
        return _adopt_with_advisory_unknown(ledger, intent_id, record, lookup)

    if record.actuator_ref and lookup.actuator_ref:
        if record.actuator_ref != lookup.actuator_ref:
            _block_unknown(ledger, intent_id, reason="ref_conflict")
            row = ledger.get(intent_id)
            if row is None:
                raise KeyError(intent_id)
            return DispatchResult(
                intent_id=intent_id,
                state=row.state,
                actuator_ref=row.actuator_ref,
            )

    target = adoption_target(record.state, lookup.state)
    try:
        if record.state != target:
            ledger.set_state(intent_id, target)
        if lookup.actuator_ref and record.actuator_ref is None:
            ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
    except IllegalStateTransitionError:
        return _finish_adopt_after_cas_race(ledger, intent_id, lookup)
    ledger.clear_inflight_marker(intent_id)
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    return DispatchResult(
        intent_id=intent_id,
        state=row.state,
        actuator_ref=row.actuator_ref,
    )


def _adopt_with_advisory_unknown(
    ledger: IntentStore,
    intent_id: str,
    record: IntentRecord,
    lookup: OwnerLookup,
) -> DispatchResult:
    _clear_epoch_unavailable_if_ok(ledger, intent_id, lookup)
    if lookup.actuator_ref and record.actuator_ref:
        if record.actuator_ref != lookup.actuator_ref:
            _block_unknown(ledger, intent_id, reason="ref_conflict")
            row = ledger.get(intent_id)
            if row is None:
                raise KeyError(intent_id)
            return DispatchResult(
                intent_id=intent_id,
                state=row.state,
                actuator_ref=row.actuator_ref,
            )
    owner_state = lookup.state or ExecutionState.accepted
    target = adoption_target(record.state, owner_state)
    try:
        if record.state != target:
            ledger.set_state(intent_id, target)
        if lookup.actuator_ref and record.actuator_ref is None:
            ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
    except IllegalStateTransitionError:
        return _finish_adopt_after_cas_race(ledger, intent_id, lookup)
    payload = record.payload or {}
    if payload.get("owner_unknown_reason") != lookup.unknown_reason:
        ledger.merge_payload(
            intent_id,
            {"owner_unknown_reason": lookup.unknown_reason},
        )
    ledger.clear_inflight_marker(intent_id)
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    return DispatchResult(
        intent_id=intent_id,
        state=row.state,
        actuator_ref=row.actuator_ref,
    )


def _reconcile_as_dispatch(ledger: IntentStore, rec: ReconcileResult) -> DispatchResult:
    row = ledger.get(rec.intent_id)
    blocked = rec.state == ExecutionState.blocked_unknown
    adopted_existing = rec.adopted_existing or (
        rec.adopted and not blocked
    )
    if blocked:
        adopted_existing = False
    return DispatchResult(
        intent_id=rec.intent_id,
        state=rec.state,
        actuator_ref=row.actuator_ref if row else None,
        redispatched=rec.redispatched,
        adopted_existing=adopted_existing,
    )


def _store_epoch_for_reserve(
    lookup: OwnerLookup,
    adapter: ActuatorAdapter,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    merged = dict(payload)
    if lookup.epoch_status == "ok" and lookup.store_epoch:
        return lookup.store_epoch, merged
    if lookup.epoch_status == "unprovisioned":
        merged["store_epoch_pending"] = True
        return "", merged
    return adapter.store_epoch(), merged


def _capture_store_epoch_if_pending(
    ledger: IntentStore, intent_id: str, lookup: OwnerLookup
) -> None:
    if lookup.epoch_status != "ok" or not lookup.store_epoch:
        return
    row = ledger.get(intent_id)
    if row is None or not row.payload or not row.payload.get("store_epoch_pending"):
        return
    if isinstance(ledger, ReferenceSqliteIntentLedger):
        ledger.apply_store_epoch_capture(intent_id, lookup.store_epoch)


def _clear_epoch_unavailable_if_ok(
    ledger: IntentStore, intent_id: str, lookup: OwnerLookup
) -> None:
    if lookup.epoch_status == "unavailable":
        return
    ledger.pop_payload_keys(intent_id, "epoch_unavailable_since")


def _finish_adopt_after_cas_race(
    ledger: IntentStore, intent_id: str, lookup: OwnerLookup
) -> DispatchResult:
    fresh = ledger.get(intent_id)
    if fresh is None:
        raise KeyError(intent_id)
    if lookup.state is None:
        return DispatchResult(
            intent_id=intent_id,
            state=fresh.state,
            actuator_ref=fresh.actuator_ref,
        )
    owner_state = lookup.state
    if owner_state == ExecutionState.outcome_unknown:
        return DispatchResult(
            intent_id=intent_id,
            state=fresh.state,
            actuator_ref=fresh.actuator_ref,
        )
    target = adoption_target(fresh.state, owner_state)
    if fresh.state != target and isinstance(ledger, ReferenceSqliteIntentLedger):
        moved = ledger.set_state_cas(intent_id, fresh.state, target)
        fresh = moved or ledger.get(intent_id) or fresh
    if lookup.actuator_ref and fresh.actuator_ref is None:
        ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
        fresh = ledger.get(intent_id) or fresh
    ledger.clear_inflight_marker(intent_id)
    return DispatchResult(
        intent_id=intent_id,
        state=fresh.state,
        actuator_ref=fresh.actuator_ref,
    )


def _lookup_epoch_unavailable(lookup: OwnerLookup) -> bool:
    return lookup.epoch_status == "unavailable"


def _retry_epoch_allows(
    record: IntentRecord, lookup: OwnerLookup, adapter: ActuatorAdapter
) -> bool:
    if lookup.epoch_status == "unavailable":
        return False
    if lookup.epoch_status == "unprovisioned" or not record.store_epoch:
        return False
    if lookup.epoch_status != "ok" or not lookup.store_epoch:
        return False
    if record.store_epoch != lookup.store_epoch:
        return False
    return adapter.admit_idempotent


def _block_unknown(ledger: IntentStore, intent_id: str, *, reason: str) -> IntentRecord:
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    if row.state == ExecutionState.blocked_unknown:
        payload = row.payload or {}
        if payload.get("blocked_reason") in STICKY_BLOCKED_REASONS:
            return row
        ledger.merge_payload(intent_id, {"blocked_reason": reason})
        return ledger.get(intent_id) or row
    blocked = ledger.block_intent_cas(intent_id, reason=reason)
    if blocked is not None:
        return blocked
    fresh = ledger.get(intent_id)
    if fresh is None:
        raise KeyError(intent_id)
    return fresh


def compensate_unknown(
    ledger: IntentStore,
    intent_id: str,
    action: CompensatingAction | None,
) -> IntentRecord:
    """Move ``blocked_unknown`` to ``outcome_unknown`` with audit (non-terminal)."""
    if action is None:
        raise PermissionError(
            "compensating action required for blocked_unknown recovery"
        )
    if action.authorized_by not in COMPENSATION_ALLOWLIST:
        raise PermissionError(f"unauthorized compensator {action.authorized_by!r}")
    record = ledger.get(intent_id)
    if record is None:
        raise KeyError(intent_id)
    if record.state != ExecutionState.blocked_unknown:
        raise ValueError(f"intent {intent_id} not blocked_unknown")
    return ledger.apply_compensation(
        intent_id,
        authorized_by=action.authorized_by,
        reason=action.reason,
        prior_state=record.state,
    )


def resolve_blocked(
    ledger: IntentStore,
    intent_id: str,
    *,
    actor: str,
    reason: str,
    target_state: ExecutionState = ExecutionState.failed,
) -> IntentRecord:
    """Terminal audited exit from any ``blocked_unknown`` row."""
    if actor not in COMPENSATION_ALLOWLIST:
        raise PermissionError(f"unauthorized resolver {actor!r}")
    if target_state not in (ExecutionState.failed, ExecutionState.cancelled):
        raise ValueError("resolve_blocked target must be failed or cancelled")
    record = ledger.get(intent_id)
    if record is None:
        raise KeyError(intent_id)
    if record.state != ExecutionState.blocked_unknown:
        raise ValueError(f"intent {intent_id} not blocked_unknown")
    return ledger.apply_blocked_resolution(
        intent_id,
        authorized_by=actor,
        reason=reason,
        target_state=target_state,
        prior_state=record.state,
    )


def resolve_ref_conflict(
    ledger: IntentStore,
    intent_id: str,
    *,
    actor: str,
    reason: str,
    target_state: ExecutionState = ExecutionState.failed,
) -> IntentRecord:
    """Terminal exit for ``blocked_unknown(ref_conflict)`` — delegates to ``resolve_blocked``."""
    record = ledger.get(intent_id)
    if record is None:
        raise KeyError(intent_id)
    payload = record.payload or {}
    if payload.get("blocked_reason") != "ref_conflict":
        raise ValueError(f"intent {intent_id} blocked_reason is not ref_conflict")
    return resolve_blocked(
        ledger,
        intent_id,
        actor=actor,
        reason=reason,
        target_state=target_state,
    )
