"""Reconcile local intent ledger state against actuator owner lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import ActuatorAdapter
from .ledger import IntentLedger, IntentRecord
from .states import ExecutionState, adoption_target

COMPENSATION_ALLOWLIST = frozenset({"conformance-reviewer", "reviewer"})


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


def _payload_admit_kwargs(record: IntentRecord) -> dict[str, Any]:
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
    """Reserve locally, admit once when owner absent, persist receipt."""
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
    if not reserved.created and record.intent_id != intent_id:
        return DispatchResult(
            intent_id=intent_id,
            state=record.state,
            actuator_ref=record.actuator_ref,
            adopted_existing=True,
        )

    lookup = adapter.lookup(identity_key)
    if lookup.found and lookup.state is not None:
        target = adoption_target(record.state, lookup.state)
        if lookup.actuator_ref:
            if record.actuator_ref and record.actuator_ref != lookup.actuator_ref:
                _block_unknown(ledger, intent_id)
                blocked = ledger.get(intent_id)
                return DispatchResult(
                    intent_id=intent_id,
                    state=blocked.state if blocked else ExecutionState.blocked_unknown,
                    actuator_ref=blocked.actuator_ref if blocked else None,
                )
            if record.actuator_ref is None:
                ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
        if record.state != target:
            ledger.set_state(intent_id, target)
        row = ledger.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return DispatchResult(
            intent_id=intent_id,
            state=row.state,
            actuator_ref=row.actuator_ref,
            adopted_existing=True,
        )

    outcome = adapter.admit(identity_key, **_payload_admit_kwargs(record))
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
        return ReconcileResult(
            intent_id=intent_id,
            state=record.state,
            adopted=False,
            redispatched=False,
            blocked=True,
        )

    lookup = adapter.lookup(record.identity_key)

    if lookup.found and lookup.state is None:
        _block_unknown(ledger, intent_id)
        row = ledger.get(intent_id)
        assert row is not None
        return ReconcileResult(
            intent_id=intent_id,
            state=row.state,
            adopted=False,
            redispatched=False,
            blocked=True,
        )

    if lookup.found and lookup.state is not None:
        if lookup.unknown_reason and _pending_local_receipt(record):
            target = adoption_target(record.state, lookup.state)
            if record.state != target:
                ledger.set_state(intent_id, target)
            if lookup.actuator_ref:
                ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
            row = ledger.get(intent_id)
            assert row is not None
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=True,
                redispatched=False,
            )
        if lookup.unknown_reason:
            _block_unknown(ledger, intent_id)
            row = ledger.get(intent_id)
            assert row is not None
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=False,
                redispatched=False,
                blocked=True,
            )
        if record.actuator_ref and lookup.actuator_ref:
            if record.actuator_ref != lookup.actuator_ref:
                _block_unknown(ledger, intent_id)
                row = ledger.get(intent_id)
                assert row is not None
                return ReconcileResult(
                    intent_id=intent_id,
                    state=row.state,
                    adopted=False,
                    redispatched=False,
                    blocked=True,
                )
        target = adoption_target(record.state, lookup.state)
        if record.state != target:
            ledger.set_state(intent_id, target)
        if lookup.actuator_ref and record.actuator_ref is None:
            ledger.patch_actuator_ref_if_null(intent_id, lookup.actuator_ref)
        row = ledger.get(intent_id)
        assert row is not None
        return ReconcileResult(
            intent_id=intent_id,
            state=row.state,
            adopted=True,
            redispatched=False,
        )

    if (
        adapter.admit_idempotent
        and _epoch_matches(record, lookup)
        and lookup.store_epoch != "missing"
    ):
        if ledger.claim_retry(intent_id):
            kwargs = _payload_admit_kwargs(record)
            outcome = adapter.admit(record.identity_key, **kwargs)
            row = ledger.record_receipt(
                intent_id,
                actuator_ref=outcome.actuator_ref,
                state=outcome.state,
            )
            return ReconcileResult(
                intent_id=intent_id,
                state=row.state,
                adopted=False,
                redispatched=True,
            )

    _block_unknown(ledger, intent_id)
    row = ledger.get(intent_id)
    assert row is not None
    return ReconcileResult(
        intent_id=intent_id,
        state=row.state,
        adopted=False,
        redispatched=False,
        blocked=True,
    )


TERMINAL_LOCAL: frozenset[ExecutionState] = frozenset(
    {
        ExecutionState.completed,
        ExecutionState.failed,
        ExecutionState.cancelled,
    }
)


def _pending_local_receipt(record: IntentRecord) -> bool:
    return record.state == ExecutionState.reserved and record.actuator_ref is None


def _epoch_matches(record: IntentRecord, lookup: Any) -> bool:
    if lookup.found:
        return True
    if not record.store_epoch or not lookup.store_epoch:
        return True
    if lookup.store_epoch == "missing":
        return False
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
    _floor_retry(ledger, intent_id)
    return _ensure_unknown(ledger, intent_id, block=True)


def _floor_retry(ledger: IntentLedger, intent_id: str) -> None:
    row = ledger.get(intent_id)
    if row is None or row.retry_count >= 1:
        return
    import sqlite3
    from contextlib import closing

    ts = row.updated_at
    with closing(sqlite3.connect(ledger.db_path, timeout=5.0)) as conn:
        conn.execute(
            "UPDATE execution_intents SET retry_count = 1, updated_at = ? "
            "WHERE intent_id = ? AND retry_count = 0",
            (ts, intent_id),
        )
        conn.commit()


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
    ledger.write_compensation(
        intent_id,
        authorized_by=action.authorized_by,
        reason=action.reason,
        prior_state=record.state,
    )
    _floor_retry(ledger, intent_id)
    return ledger.set_state(intent_id, ExecutionState.outcome_unknown)
