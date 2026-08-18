"""Autonomous activation verification after a drain-gated GIW kill."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from charter_runner_store.propagation_activation_events import (
    ManageRestartActivationProgress,
    ManageRestartActivationUnverified,
    ManageRestartActivationValidated,
    ManageRestartVerifying,
    publish_activation_event,
)
from charter_runner_store.propagation_validation import (
    advance_validation,
    get_validation,
    latest_validation_for_intent,
    mint_pending_validation_for_intent,
    set_kill_boundary,
)
from universal_logging import get_logger

from .restart_intent_states import (
    STATUS_ACTIVATION_UNVERIFIED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_PENDING_DRAIN,
    STATUS_VERIFYING_ACTIVATION,
)
from .restart_intent_store import Intent, RestartIntentStore

logger = get_logger(__name__)

ACTIVATION_IDLE_TIMEOUT_S = 120.0
_VERIFY_TASKS: set[asyncio.Task[None]] = set()
_VERIFY_ACTIONS = frozenset({"restart", "sync_restart"})


def mint_activation_validation(
    store: RestartIntentStore,
    intent: Intent,
    *,
    code_ref: str = "HEAD",
    row_id: str | None = None,
) -> str:
    """Reuse a compatible pending validation, or mint one keyed to ``code_ref``.

    ``code_ref`` is the propagate row's commit SHA when the caller has one.
    Same-intent reuse is refused when the pending is already bound to a
    different ledger ``row_id`` (the 5139a3e6-shaped collision). Same-key
    occupied pendings are superseded then replaced; distinct keys insert
    alongside. Returns the validation id used in the deferred 202 envelope.
    """
    existing = latest_validation_for_intent(intent.intent_id)
    if existing is not None and existing.outcome == "pending":
        occupied = existing.row_id
        if occupied is None or (row_id is not None and occupied == row_id):
            return existing.validation_id
    return mint_pending_validation_for_intent(
        intent,
        code_ref=code_ref,
        row_id=row_id,
        advance_intent_fn=store.advance_if_status,
    )


def _persist_kill_boundary(
    store: RestartIntentStore,
    intent: Intent,
    *,
    boundary_source: str,
) -> tuple[str, Any | None]:
    """Record kill boundary on intent and bound validation row when present."""
    kill_boundary_at = datetime.now(UTC).isoformat()
    kill_mono = time.monotonic()
    store.set_kill_boundary(intent.intent_id, kill_boundary_at=kill_boundary_at)
    validation = latest_validation_for_intent(intent.intent_id)
    if validation is not None:
        set_kill_boundary(
            validation.validation_id,
            kill_boundary_at=kill_boundary_at,
            boundary_source=boundary_source,
            restart_boundary_monotonic=kill_mono,
        )
    return kill_boundary_at, validation


async def record_kill_boundary_and_arm_verify(
    store: RestartIntentStore,
    intent: Intent,
    *,
    boundary_source: str,
    from_status: str = STATUS_DRAINED_RESTARTING,
) -> None:
    """Persist kill boundary, emit verifying event, and schedule activation verify."""
    kill_boundary_at, validation = _persist_kill_boundary(
        store, intent, boundary_source=boundary_source
    )
    if not arms_activation_verify(intent.action):
        store.advance(intent.intent_id, status=STATUS_COMPLETED)
        return
    cas_ok = store.advance_if_status(
        intent.intent_id,
        from_status=from_status,
        to_status=STATUS_VERIFYING_ACTIVATION,
    )
    if not cas_ok:
        return
    if validation is None:
        _terminal_unverified_no_validation(
            store, intent.intent_id, reason="missing_validation_at_arm"
        )
        return
    publish_activation_event(
        ManageRestartVerifying(
            intent_id=intent.intent_id,
            validation_id=validation.validation_id,
            service=intent.service,
            kill_boundary_at=kill_boundary_at,
            boundary_source=boundary_source,
        )
    )
    schedule_activation_verify(store, intent.intent_id, validation.validation_id)


async def arm_verify_after_generation_gone(
    store: RestartIntentStore,
    intent: Intent,
) -> bool:
    """Generation-gone path: record boundary and arm verify without SIGTERM.

    Returns True when the intent reached a state requiring no further caller
    action (normal arm or self-terminalization). Returns False when verification
    does not apply to this action and the caller may treat the intent as completed.
    """
    _, validation = _persist_kill_boundary(
        store, intent, boundary_source="generation_gone"
    )
    if not arms_activation_verify(intent.action):
        return False
    cas_ok = store.advance_if_status(
        intent.intent_id,
        from_status=STATUS_PENDING_DRAIN,
        to_status=STATUS_VERIFYING_ACTIVATION,
    )
    if not cas_ok:
        return False
    if validation is None:
        _terminal_unverified_no_validation(
            store, intent.intent_id, reason="missing_validation_at_arm"
        )
        return True
    schedule_activation_verify(store, intent.intent_id, validation.validation_id)
    return True


def _monotonic_from_kill_boundary(kill_boundary_at: str | None) -> float | None:
    if not kill_boundary_at:
        return None
    try:
        boundary_dt = datetime.fromisoformat(kill_boundary_at.replace("Z", "+00:00"))
        delta = (datetime.now(UTC) - boundary_dt).total_seconds()
        return time.monotonic() - delta
    except ValueError:
        return None


def _observation_class(payload: dict[str, Any] | None) -> str:
    if payload is None or payload.get("probe_reachable") is False:
        return "unreachable"
    return "reachable"


async def _invoke_activation_settle(
    store: RestartIntentStore,
    intent_id: str,
    validation_id: str,
) -> None:
    """Ready-join → settle → close_row before terminal activation validation."""
    from .propagation_settle_hook import invoke_propagation_settle_for_service

    intent = store.get(intent_id)
    validation = get_validation(validation_id)
    if intent is None or validation is None:
        return
    boundary = validation.restart_boundary_monotonic
    if boundary is None:
        boundary = _monotonic_from_kill_boundary(intent.kill_boundary_at)
    if boundary is None:
        boundary = time.monotonic()
    await invoke_propagation_settle_for_service(
        intent.service,
        settle_not_before_monotonic=boundary,
        source="drain",
        restart_intent=intent_id,
        validation_id=validation_id,
    )


async def run_activation_verify(
    store: RestartIntentStore,
    intent_id: str,
    validation_id: str,
    *,
    idle_timeout_s: float = ACTIVATION_IDLE_TIMEOUT_S,
) -> None:
    """Drive one verifying intent to a terminal validation outcome."""
    intent = store.get(intent_id)
    validation = get_validation(validation_id)
    if intent is None or validation is None:
        return
    if _abort_if_not_verifying(store, intent, validation, validation_id):
        return
    if not intent.kill_boundary_at:
        _terminal_unverified(store, intent_id, validation_id, "missing_kill_boundary")
        return
    await _invoke_activation_settle(store, intent_id, validation_id)
    intent = store.get(intent_id)
    validation = get_validation(validation_id)
    if intent is None or validation is None:
        return
    if _abort_if_not_verifying(store, intent, validation, validation_id):
        return
    settle_mono = (
        validation.restart_boundary_monotonic
        or _monotonic_from_kill_boundary(intent.kill_boundary_at)
        or time.monotonic()
    )
    idle_deadline = settle_mono + idle_timeout_s
    last_class = _observation_class(validation.pre_observation)
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        IdentityMeasurementError,
        probe_process_live,
    )

    while True:
        intent = store.get(intent_id)
        validation = get_validation(validation_id)
        if intent is None or validation is None:
            return
        if _abort_if_not_verifying(store, intent, validation, validation_id):
            return
        payload = probe_process_live(intent.service)
        obs_class = _observation_class(payload)
        if obs_class != last_class:
            publish_activation_event(
                ManageRestartActivationProgress(
                    intent_id=intent_id,
                    validation_id=validation_id,
                    progress_class=obs_class,
                )
            )
            last_class = obs_class
            idle_deadline = time.monotonic() + idle_timeout_s
        if payload is not None:
            try:
                from deploy_identity.code_ref_relation import (
                    code_ref_relation_from_observed,
                )

                from services.git_integration_worker.cursor_auto.propagation_probe import (
                    resolve_identity_measurement,
                )

                observed = payload.get("code_version")
                relation = code_ref_relation_from_observed(validation.code_ref, observed)
                identity = resolve_identity_measurement(
                    {"proof_before": validation.pre_observation, **payload},
                    service=intent.service,
                    proof_class="process_live",
                    code_ref=validation.code_ref,
                    open_row_payload=validation.pre_observation,
                )
                if identity in {"changed", "measured"} and relation in {"equal", "ancestor"}:
                    if advance_validation(
                        validation_id,
                        outcome="validated",
                        post_observation=payload,
                        observed_code_version=str(observed) if observed else None,
                        code_ref_relation=relation,
                        identity_measurement=identity,
                    ):
                        store.advance_if_status(
                            intent_id,
                            from_status=STATUS_VERIFYING_ACTIVATION,
                            to_status=STATUS_COMPLETED,
                        )
                        publish_activation_event(
                            ManageRestartActivationValidated(
                                intent_id=intent_id,
                                validation_id=validation_id,
                                code_ref_relation=relation,
                                identity_measurement=identity,
                            )
                        )
                        return
            except IdentityMeasurementError:
                pass
        if time.monotonic() >= idle_deadline:
            _terminal_unverified(store, intent_id, validation_id, "idle_timeout")
            return
        await asyncio.sleep(0.5)


def _terminal_unverified_no_validation(
    store: RestartIntentStore,
    intent_id: str,
    *,
    reason: str,
) -> None:
    """Terminalize a verifying intent that has no validation row."""
    if store.advance_if_status(
        intent_id,
        from_status=STATUS_VERIFYING_ACTIVATION,
        to_status=STATUS_ACTIVATION_UNVERIFIED,
        reason=reason,
    ):
        publish_activation_event(
            ManageRestartActivationUnverified(
                intent_id=intent_id,
                validation_id=None,
                outcome="unvalidated_timeout",
                failure_reason=reason,
            )
        )


def _abort_if_not_verifying(
    store: RestartIntentStore,
    intent: Intent,
    validation: Any,
    validation_id: str,
) -> bool:
    """True => caller should return now.

    When the intent is still verifying_activation but its validation already
    resolved elsewhere, terminalize the intent before returning.
    """
    if intent.status != STATUS_VERIFYING_ACTIVATION:
        return True
    if validation.outcome != "pending":
        _terminal_unverified(
            store,
            intent.intent_id,
            validation_id,
            f"validation_resolved_{validation.outcome}",
        )
        return True
    return False


def _terminal_unverified(
    store: RestartIntentStore,
    intent_id: str,
    validation_id: str,
    reason: str,
) -> None:
    advance_validation(
        validation_id,
        outcome="unvalidated_timeout",
        failure_reason=reason,
    )
    store.advance_if_status(
        intent_id,
        from_status=STATUS_VERIFYING_ACTIVATION,
        to_status=STATUS_ACTIVATION_UNVERIFIED,
        reason=reason,
    )
    publish_activation_event(
        ManageRestartActivationUnverified(
            intent_id=intent_id,
            validation_id=validation_id,
            outcome="unvalidated_timeout",
            failure_reason=reason,
        )
    )


def schedule_activation_verify(
    store: RestartIntentStore, intent_id: str, validation_id: str
) -> None:
    """Schedule verify on a tracked task that does not hold the restart gate."""
    async def _task() -> None:
        try:
            await run_activation_verify(store, intent_id, validation_id)
        except Exception:
            logger.exception(
                "activation verify failed intent_id=%s validation_id=%s",
                intent_id,
                validation_id,
            )

    task = asyncio.create_task(_task())
    _VERIFY_TASKS.add(task)
    task.add_done_callback(_VERIFY_TASKS.discard)


async def resume_activation_verify(
    store: RestartIntentStore,
    intent_id: str,
) -> None:
    """Boot resume: re-enter verify without begin-drain or kill."""
    validation = latest_validation_for_intent(intent_id)
    if validation is None:
        _terminal_unverified_no_validation(
            store, intent_id, reason="missing_validation_at_boot_resume"
        )
        return
    await run_activation_verify(store, intent_id, validation.validation_id)


def arms_activation_verify(action: str) -> bool:
    """Return whether manage must drive post-kill activation verification."""
    return action in _VERIFY_ACTIONS


__all__ = [
    "ACTIVATION_IDLE_TIMEOUT_S", "arms_activation_verify", "mint_activation_validation",
    "record_kill_boundary_and_arm_verify", "arm_verify_after_generation_gone",
    "resume_activation_verify", "run_activation_verify", "schedule_activation_verify",
]
