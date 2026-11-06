"""Crash-injection helpers for conformance tests (not collected as tests)."""

from __future__ import annotations

from typing import Any

from .adapters import ActuatorAdapter
from .ledger import IntentStore
from .reconciler import DispatchResult, payload_admit_kwargs
from .states import ExecutionState


def dispatch_crash_after_reserve(
    ledger: IntentStore,
    adapter: ActuatorAdapter,
    *,
    intent_id: str,
    identity_key: str,
    admit_kwargs: dict[str, Any] | None = None,
) -> DispatchResult:
    payload = {"admit_kwargs": admit_kwargs or {}}
    reserved = ledger.reserve(
        intent_id=intent_id,
        actuator=adapter.name,  # type: ignore[arg-type]
        identity_key=identity_key,
        store_epoch=adapter.store_epoch(),
        payload=payload,
    )
    row = reserved.record
    return DispatchResult(
        intent_id=intent_id,
        state=row.state,
        actuator_ref=row.actuator_ref,
        adopted_existing=not reserved.created,
    )


def dispatch_crash_after_admit_before_receipt(
    ledger: IntentStore,
    adapter: ActuatorAdapter,
    *,
    intent_id: str,
    identity_key: str,
    admit_kwargs: dict[str, Any] | None = None,
) -> DispatchResult:
    payload = {"admit_kwargs": admit_kwargs or {}}
    reserved = ledger.reserve(
        intent_id=intent_id,
        actuator=adapter.name,  # type: ignore[arg-type]
        identity_key=identity_key,
        store_epoch=adapter.store_epoch(),
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
    kwargs = payload_admit_kwargs(record) or (admit_kwargs or {})
    outcome = adapter.admit(identity_key, **kwargs)
    return DispatchResult(
        intent_id=intent_id,
        state=ExecutionState.reserved,
        actuator_ref=outcome.actuator_ref,
    )
