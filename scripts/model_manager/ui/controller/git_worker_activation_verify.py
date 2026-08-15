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
    publish_activation_event,
)
from charter_runner_store.propagation_validation import (
    advance_validation,
    get_validation,
    latest_validation_for_intent,
)
from universal_logging import get_logger

from .restart_intent_states import (
    STATUS_ACTIVATION_UNVERIFIED,
    STATUS_COMPLETED,
    STATUS_VERIFYING_ACTIVATION,
)
from .restart_intent_store import RestartIntentStore

logger = get_logger(__name__)

ACTIVATION_IDLE_TIMEOUT_S = 120.0
_VERIFY_TASKS: set[asyncio.Task[None]] = set()
_VERIFY_ACTIONS = frozenset({"restart", "sync_restart"})


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
    if payload is None:
        return "unreachable"
    if payload.get("probe_reachable") is False:
        return "unreachable"
    return "reachable"


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
    if intent.status != STATUS_VERIFYING_ACTIVATION or validation.outcome != "pending":
        return
    if not intent.kill_boundary_at:
        _terminal_unverified(store, intent_id, validation_id, "missing_kill_boundary")
        return
    settle_mono = _monotonic_from_kill_boundary(intent.kill_boundary_at) or time.monotonic()
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
        if intent.status != STATUS_VERIFYING_ACTIVATION or validation.outcome != "pending":
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
    store: RestartIntentStore,
    intent_id: str,
    validation_id: str,
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
        return
    await run_activation_verify(store, intent_id, validation.validation_id)


def arms_activation_verify(action: str) -> bool:
    return action in _VERIFY_ACTIONS


__all__ = [
    "ACTIVATION_IDLE_TIMEOUT_S",
    "arms_activation_verify",
    "resume_activation_verify",
    "run_activation_verify",
    "schedule_activation_verify",
]
