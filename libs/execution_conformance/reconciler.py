"""Reconcile local intent ledger state against actuator owner lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import ActuatorAdapter, OwnerLookup
from .ledger import IntentLedger, IntentRecord
from .states import (
    TERMINAL_STATES,
    ExecutionState,
    IllegalStateTransitionError,
    adoption_target,
)

COMPENSATION_ALLOWLIST = frozenset({"conformance-reviewer", "reviewer"})

TERMINAL_LOCAL: frozenset[ExecutionState] = TERMINAL_STATES


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


def payload_admit_kwargs(record: IntentRecord) -> dict[str, Any]:
    if not record.payload:
        return {}
    raw = record.payload.get("admit_kwargs")
    return dict(raw) if isinstance(raw, dict) else {}


def dispatch_intent(
    ledger: IntentLedger,
    adapter: ActuatorAdapter,
    *,
    intent_id: str,
    identity_key: str,
    admit_kwargs: dict[str, Any] | None = None,
) -> DispatchResult:
    """Reserve locally; admit only on a newly created row with owner miss."""
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
    adopted = _try_adopt_from_lookup(ledger, intent_id, record, lookup)
    if adopted is not None:
        return adopted
    if lookup.found:
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return DispatchResult(
            intent_id=intent_id,
            state=row.state,
            actuator_ref=row.actuator_ref,
        )

    outcome = adapter.admit(identity_key, **payload_admit_kwargs(record))
    row = ledger.record_receipt(
        intent_id,
        actuator_ref=outcome.actuator_ref,
        state=outcome.state,
    )
    return DispatchResult(
        intent_id=intent_id,
        state=row.state,
        actuator_ref=row.actuator_ref,
    )


def reconcile(
    ledger: IntentLedger,
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

    if lookup.found:
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return ReconcileResult(
            intent_id=intent_id,
            state=row.state,
            adopted=False,
            redispatched=False,
            blocked=row.state == ExecutionState.blocked_unknown,
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
                row = ledger.get(intent_id)
                if row is None:
                    raise
                return ReconcileResult(
                    intent_id=intent_id,
                    state=row.state,
                    adopted=False,
                    redispatched=row.actuator_ref is not None,
                )
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=False,
                redispatched=True,
            )

    _block_unknown(ledger, intent_id)
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


def _reconcile_blocked_unknown(
    ledger: IntentLedger,
    adapter: ActuatorAdapter,
    intent_id: str,
    record: IntentRecord,
) -> ReconcileResult:
    lookup = adapter.lookup(record.identity_key)
    if lookup.found and lookup.state in TERMINAL_LOCAL and not lookup.unknown_reason:
        target = lookup.state
        if record.state != target:
            ledger.set_state(intent_id, target)
        if lookup.actuator_ref:
            ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
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


def _try_adopt_from_lookup(
    ledger: IntentLedger,
    intent_id: str,
    record: IntentRecord,
    lookup: OwnerLookup,
) -> DispatchResult | None:
    if lookup.found and lookup.state is None:
        _block_unknown(ledger, intent_id)
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return DispatchResult(
            intent_id=intent_id,
            state=row.state,
            actuator_ref=row.actuator_ref,
        )

    if not lookup.found or lookup.state is None:
        return None

    if lookup.state == ExecutionState.outcome_unknown:
        _block_unknown(ledger, intent_id)
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return DispatchResult(
            intent_id=intent_id,
            state=row.state,
            actuator_ref=row.actuator_ref,
        )

    if lookup.unknown_reason:
        if _pending_local_receipt(record):
            target = adoption_target(record.state, lookup.state)
            if record.state != target:
                ledger.set_state(intent_id, target)
            if lookup.actuator_ref:
                ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
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
        _block_unknown(ledger, intent_id)
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return DispatchResult(
            intent_id=intent_id,
            state=row.state,
            actuator_ref=row.actuator_ref,
        )

    if record.actuator_ref and lookup.actuator_ref:
        if record.actuator_ref != lookup.actuator_ref:
            _block_unknown(ledger, intent_id)
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


def _reconcile_as_dispatch(
    ledger: IntentLedger, rec: ReconcileResult
) -> DispatchResult:
    row = ledger.get(rec.intent_id)
    return DispatchResult(
        intent_id=rec.intent_id,
        state=rec.state,
        actuator_ref=row.actuator_ref if row else None,
        redispatched=rec.redispatched,
        adopted_existing=rec.adopted,
    )


def _pending_local_receipt(record: IntentRecord) -> bool:
    return record.state == ExecutionState.reserved and record.actuator_ref is None


def _retry_epoch_allows(
    record: IntentRecord, lookup: OwnerLookup, adapter: ActuatorAdapter
) -> bool:
    if not record.store_epoch or lookup.store_epoch in ("", "missing"):
        return False
    if adapter.admit_idempotent:
        return True
    return record.store_epoch == lookup.store_epoch


def _ensure_unknown(
    ledger: IntentLedger, intent_id: str, *, block: bool
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


def _block_unknown(ledger: IntentLedger, intent_id: str) -> IntentRecord:
    ledger.floor_retry(intent_id)
    return _ensure_unknown(ledger, intent_id, block=True)


def compensate_unknown(
    ledger: IntentLedger,
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
