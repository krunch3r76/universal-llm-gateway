"""Reconcile local intent ledger state against actuator owner lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import ActuatorAdapter, OwnerLookup, validate_actuator_identity_key
from .ledger import IntentRecord, IntentStore
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


@dataclass(frozen=True, slots=True)
class CompensatingAction:
    authorized_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class DispatchResult:
    intent_id: str
    state: ExecutionState
    actuator_ref: str | None
    redispatched: bool = False
    adopted_existing: bool = False


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    intent_id: str
    state: ExecutionState
    adopted: bool
    redispatched: bool
    blocked: bool = False
    transient: bool = False


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
) -> DispatchResult:
    """Reserve locally; admit only on ``reserve().created`` with owner miss."""
    validate_actuator_identity_key(adapter, identity_key)
    payload = {"admit_kwargs": admit_kwargs or {}}
    epoch = adapter.store_epoch()
    reserved = ledger.reserve(
        intent_id=intent_id,
        actuator=adapter.name,  # type: ignore[arg-type]
        identity_key=identity_key,
        store_epoch=epoch,
        payload=payload,
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
        rec = reconcile(ledger, adapter, intent_id)
        return _reconcile_as_dispatch(ledger, rec)

    lookup = adapter.lookup(identity_key)
    if _lookup_epoch_unavailable(lookup):
        return DispatchResult(
            intent_id=intent_id,
            state=record.state,
            actuator_ref=record.actuator_ref,
        )

    adopted = _try_adopt_from_lookup(ledger, intent_id, record, lookup)
    if adopted is not None:
        return adopted

    outcome = adapter.admit(identity_key, **payload_admit_kwargs(record))
    try:
        row = ledger.record_receipt(
            intent_id,
            actuator_ref=outcome.actuator_ref,
            state=outcome.state,
        )
    except IllegalStateTransitionError:
        rec = reconcile(ledger, adapter, intent_id)
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
) -> ReconcileResult:
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
        return _reconcile_blocked_unknown(ledger, adapter, intent_id, record)

    lookup = adapter.lookup(record.identity_key)
    if _lookup_epoch_unavailable(lookup):
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            redispatched=False,
            blocked=False,
            transient=True,
        )

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
            redispatched=False,
            blocked=blocked,
        )

    if adapter.admit_idempotent and _retry_epoch_allows(record, lookup, adapter):
        if ledger.claim_retry(intent_id):
            kwargs = payload_admit_kwargs(record)
            outcome = adapter.admit(record.identity_key, **kwargs)
            try:
                row = ledger.record_receipt(
                    intent_id,
                    actuator_ref=outcome.actuator_ref,
                    state=outcome.state,
                )
            except IllegalStateTransitionError:
                return _result_after_retry_race(ledger, intent_id)
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=False,
                redispatched=True,
            )
        return _result_after_retry_race(ledger, intent_id)

    if _lookup_epoch_unavailable(lookup):
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            redispatched=False,
            blocked=False,
            transient=True,
        )

    reason = _miss_block_reason(record, lookup)
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


def _result_after_retry_race(ledger: IntentStore, intent_id: str) -> ReconcileResult:
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    if row.state in IN_FLIGHT or row.state in TERMINAL_LOCAL:
        if row.actuator_ref is not None or row.state in TERMINAL_LOCAL:
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=row.state in TERMINAL_LOCAL or row.actuator_ref is not None,
                redispatched=row.actuator_ref is not None
                and row.state != ExecutionState.reserved,
                blocked=False,
            )
    return ReconcileResult(
        intent_id=intent_id,
        state=row.state,
        adopted=False,
        redispatched=False,
        blocked=False,
        transient=True,
    )


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
) -> ReconcileResult:
    payload = record.payload or {}
    blocked_reason = payload.get("blocked_reason")
    if blocked_reason == "ref_conflict":
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            redispatched=False,
            blocked=True,
        )

    lookup = adapter.lookup(record.identity_key)
    if _lookup_epoch_unavailable(lookup):
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            redispatched=False,
            blocked=True,
            transient=True,
        )

    if (
        lookup.found
        and not lookup.unknown_reason
        and lookup.state
        not in (
            None,
            ExecutionState.outcome_unknown,
        )
    ):
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
        if record.actuator_ref is None or record.actuator_ref == lookup.actuator_ref:
            target = lookup.state
            row = _promote_from_blocked(
                ledger,
                intent_id,
                target=target,
                actuator_ref=lookup.actuator_ref,
            )
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=True,
                redispatched=False,
                blocked=False,
            )

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


def _promote_from_blocked(
    ledger: IntentStore,
    intent_id: str,
    *,
    target: ExecutionState,
    actuator_ref: str | None,
) -> IntentRecord:
    ledger.set_state(intent_id, ExecutionState.outcome_unknown)
    ledger.set_state(intent_id, target)
    if actuator_ref:
        ledger.patch_actuator_ref_if_null(intent_id, actuator_ref)
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    return row


def _try_adopt_from_lookup(
    ledger: IntentStore,
    intent_id: str,
    record: IntentRecord,
    lookup: OwnerLookup,
) -> DispatchResult | None:
    if not lookup.found:
        return None

    if lookup.state is None or lookup.state == ExecutionState.outcome_unknown:
        _block_unknown(ledger, intent_id, reason="owner_state_unknown")
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
    if record.state != target:
        ledger.set_state(intent_id, target)
    if lookup.actuator_ref and record.actuator_ref is None:
        ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
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
    if record.state != target:
        ledger.set_state(intent_id, target)
    if lookup.actuator_ref and record.actuator_ref is None:
        ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
    payload = record.payload or {}
    if payload.get("owner_unknown_reason") != lookup.unknown_reason:
        ledger.merge_payload(
            intent_id,
            {"owner_unknown_reason": lookup.unknown_reason},
        )
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
    return DispatchResult(
        intent_id=rec.intent_id,
        state=rec.state,
        actuator_ref=row.actuator_ref if row else None,
        redispatched=rec.redispatched,
        adopted_existing=rec.adopted,
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


def _ensure_unknown(
    ledger: IntentStore, intent_id: str, *, block: bool
) -> IntentRecord:
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    if row.state == ExecutionState.blocked_unknown:
        return row
    if row.state == ExecutionState.outcome_unknown:
        if block:
            return ledger.set_state(intent_id, ExecutionState.blocked_unknown)
        return row
    row = ledger.set_state(intent_id, ExecutionState.outcome_unknown)
    if block:
        return ledger.set_state(intent_id, ExecutionState.blocked_unknown)
    return row


def _block_unknown(ledger: IntentStore, intent_id: str, *, reason: str) -> IntentRecord:
    row = ledger.get(intent_id)
    if row is None:
        raise KeyError(intent_id)
    payload = row.payload or {}
    existing = payload.get("blocked_reason")
    if existing == "ref_conflict":
        reason = "ref_conflict"
    elif reason != "ref_conflict" and existing:
        pass
    ledger.floor_retry(intent_id)
    ledger.merge_payload(intent_id, {"blocked_reason": reason})
    return _ensure_unknown(ledger, intent_id, block=True)


def compensate_unknown(
    ledger: IntentStore,
    intent_id: str,
    action: CompensatingAction | None,
) -> IntentRecord:
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


def resolve_ref_conflict(
    ledger: IntentStore,
    intent_id: str,
    *,
    actor: str,
    reason: str,
    target_state: ExecutionState = ExecutionState.failed,
) -> IntentRecord:
    """Terminal exit for ``blocked_unknown(ref_conflict)`` with audit row."""
    if actor not in COMPENSATION_ALLOWLIST:
        raise PermissionError(f"unauthorized resolver {actor!r}")
    record = ledger.get(intent_id)
    if record is None:
        raise KeyError(intent_id)
    payload = record.payload or {}
    if record.state != ExecutionState.blocked_unknown:
        raise ValueError(f"intent {intent_id} not blocked_unknown")
    if payload.get("blocked_reason") != "ref_conflict":
        raise ValueError(f"intent {intent_id} blocked_reason is not ref_conflict")
    return ledger.apply_ref_conflict_resolution(
        intent_id,
        authorized_by=actor,
        reason=reason,
        target_state=target_state,
        prior_state=record.state,
    )
