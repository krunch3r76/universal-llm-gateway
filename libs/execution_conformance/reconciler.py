"""Reconcile local intent ledger state against actuator owner lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import ActuatorAdapter, AdmitOutcome
from .ledger import IntentLedger
from .manifest import owner_storage_durable, owner_storage_memory_only
from .states import ExecutionState


@dataclass(frozen=True, slots=True)
class CompensatingAction:
    """Explicit authorization to mutate a blocked_unknown intent."""

    authorized_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class DispatchResult:
    intent_id: str
    state: ExecutionState
    actuator_ref: str | None
    redispatched: bool = False


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    intent_id: str
    state: ExecutionState
    adopted: bool
    redispatched: bool
    blocked: bool = False


def dispatch_intent(
    ledger: IntentLedger,
    adapter: ActuatorAdapter,
    *,
    intent_id: str,
    identity_key: str,
    payload: dict[str, Any] | None = None,
    admit_kwargs: dict[str, Any] | None = None,
    remote_admit: bool = True,
    record_receipt: bool = True,
) -> DispatchResult:
    """Reserve locally, optionally admit remotely, optionally persist receipt."""
    ledger.reserve(
        intent_id=intent_id,
        actuator=adapter.name,
        identity_key=identity_key,
        payload=payload,
    )
    if not remote_admit:
        row = ledger.get(intent_id)
        assert row is not None
        return DispatchResult(
            intent_id=intent_id, state=row.state, actuator_ref=row.actuator_ref
        )

    outcome = adapter.admit(identity_key, **(admit_kwargs or {}))
    if not record_receipt:
        return DispatchResult(
            intent_id=intent_id,
            state=ExecutionState.reserved,
            actuator_ref=outcome.actuator_ref,
        )
    if record_receipt:
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
    raise RuntimeError("unreachable dispatch_intent branch")


def reconcile(
    ledger: IntentLedger,
    adapter: ActuatorAdapter,
    intent_id: str,
) -> ReconcileResult:
    """Query owner by identity; adopt, single keyed retry, or block unknown."""
    record = ledger.get(intent_id)
    if record is None:
        raise KeyError(f"unknown intent {intent_id!r}")

    lookup = adapter.lookup(record.identity_key)
    actuator = record.actuator  # type: ignore[assignment]
    assert isinstance(actuator, str)

    if lookup.found and lookup.state is not None:
        if _cdp_open_leg_outcome_unknown(actuator, record, record.identity_key):
            ledger.set_state(intent_id, ExecutionState.outcome_unknown)
            final = ledger.get(intent_id)
            assert final is not None
            return ReconcileResult(
                intent_id=intent_id,
                state=final.state,
                adopted=False,
                redispatched=False,
                blocked=False,
            )
        if _bridge_msg_without_reply(record, lookup):
            ledger.set_state(intent_id, ExecutionState.outcome_unknown)
            ledger.set_state(intent_id, ExecutionState.blocked_unknown)
            return ReconcileResult(
                intent_id=intent_id,
                state=ExecutionState.blocked_unknown,
                adopted=False,
                redispatched=False,
                blocked=True,
            )
        target = lookup.state
        if record.state != target:
            ledger.set_state(intent_id, target)
        if lookup.actuator_ref and record.actuator_ref != lookup.actuator_ref:
            ledger.patch_actuator_ref(intent_id, lookup.actuator_ref)
        return ReconcileResult(
            intent_id=intent_id,
            state=target,
            adopted=True,
            redispatched=False,
        )

    if owner_storage_memory_only(actuator):  # type: ignore[arg-type]
        ledger.set_state(intent_id, ExecutionState.outcome_unknown)
        ledger.set_state(intent_id, ExecutionState.blocked_unknown)
        return ReconcileResult(
            intent_id=intent_id,
            state=ExecutionState.blocked_unknown,
            adopted=False,
            redispatched=False,
            blocked=True,
        )

    if owner_storage_durable(actuator):  # type: ignore[arg-type]
        if record.retry_count >= 1:
            ledger.set_state(intent_id, ExecutionState.outcome_unknown)
            ledger.set_state(intent_id, ExecutionState.blocked_unknown)
            return ReconcileResult(
                intent_id=intent_id,
                state=ExecutionState.blocked_unknown,
                adopted=False,
                redispatched=False,
                blocked=True,
            )
        ledger.increment_retry(intent_id)
        outcome = adapter.admit(record.identity_key)
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

    ledger.set_state(intent_id, ExecutionState.outcome_unknown)
    ledger.set_state(intent_id, ExecutionState.blocked_unknown)
    return ReconcileResult(
        intent_id=intent_id,
        state=ExecutionState.blocked_unknown,
        adopted=False,
        redispatched=False,
        blocked=True,
    )


@dataclass(frozen=True, slots=True)
class IntentRecordState:
    intent_id: str
    state: ExecutionState
    authorized_by: str


def _cdp_open_leg_outcome_unknown(
    actuator: str, record: Any, identity_key: str
) -> bool:
    if actuator != "cdp_ask":
        return False
    if record.state == ExecutionState.reserved and record.actuator_ref is None:
        return False
    from systems.frontier_consult.cdp_generate_inflight_ledger import read_inflight_leg

    leg = read_inflight_leg(identity_key)
    return (
        leg is not None
        and not leg.proof_emitted
        and not leg.delivered
        and not leg.abandoned
    )


def _bridge_msg_without_reply(record: Any, lookup: Any) -> bool:
    if record.state == ExecutionState.reserved and record.actuator_ref is None:
        return False
    return bool(
        lookup.found
        and lookup.state == ExecutionState.running
        and lookup.note
        and "REPLY" in lookup.note
    )


def compensate_unknown(
    ledger: IntentLedger,
    intent_id: str,
    action: CompensatingAction | None,
) -> IntentRecordState:
    if action is None:
        raise PermissionError(
            "compensating action required for blocked_unknown recovery"
        )
    record = ledger.get(intent_id)
    if record is None:
        raise KeyError(intent_id)
    if record.state != ExecutionState.blocked_unknown:
        raise ValueError(f"intent {intent_id} not blocked_unknown")
    row = ledger.set_state(intent_id, ExecutionState.outcome_unknown)
    return IntentRecordState(
        intent_id=row.intent_id,
        state=row.state,
        authorized_by=action.authorized_by,
    )


def health_only_would_claim_ready(_adapter: ActuatorAdapter) -> bool:
    """Falsifier helper: liveness without owner lookup must not satisfy reconcile."""
    return False


def admit_once(
    adapter: ActuatorAdapter,
    identity_key: str,
    **kwargs: Any,
) -> AdmitOutcome:
    return adapter.admit(identity_key, **kwargs)
