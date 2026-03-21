"""State transition helpers for model lifecycle management.

Provides functions for handling state machine transitions during model
loading, idle, and error recovery operations.

Status is derived from the WorkerStateMachine — these functions perform SM
transitions only. Status auto-derives via ModelResourceInfo.status property.
"""

import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

from src.core.workers.state_machine import WorkerState, WorkerStateMachine

from .types import ModelResourceInfo, ModelStatus

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)


def handle_error_state_recovery(
    state_machines: dict[str, WorkerStateMachine],
    models: dict[str, ModelResourceInfo],
    key: str,
    model_id: str,
) -> None:
    """Clear ERROR state if present to allow retry.

    SM clear_error transitions to UNLOADED; status auto-derives to NOT_LOADED.
    SM on_transition callback handles event emission and error_message clearing.

    Args:
        state_machines: Dict mapping normalized string keys to their state machines.
        models: Dict mapping normalized string keys to their resource info.
        key: The normalized string key for lookups.
        model_id: The original model ID string for logging.
    """
    if key not in models:
        return

    if models[key].status != ModelStatus.ERROR:
        return

    error_msg = models[key].error_message or "unknown"
    logger.info(
        f"Clearing ERROR state for {model_id} (previous: {error_msg}) to allow retry"
    )

    if key in state_machines:
        success = state_machines[key].clear_error("Retry load attempt")
        if success:
            logger.debug(f"Cleared error state for {model_id}")
        else:
            logger.warning(f"Failed to clear error state for {model_id}")
    else:
        logger.warning(
            f"State machine not found for {model_id} — cannot clear ERROR"
        )


def transition_to_loading(
    state_machines: dict[str, WorkerStateMachine],
    key: str,
    model_id: str,
) -> bool:
    """Transition model SM to LOADING state. Status auto-derives.

    Returns:
        True if transitioned to LOADING, already in LOADING, or no SM exists.
        False if SM rejected the transition (caller must abort load).
    """
    if key in state_machines:
        sm = state_machines[key]
        if sm.current_state == WorkerState.LOADING:
            return True
        current = sm.current_state
        success = sm.transition(WorkerState.LOADING, reason="model_loading_started")
        if not success:
            logger.error(
                f"Failed to transition {model_id} to LOADING "
                f"(current_state={current.value})"
            )
            return False
    return True


def transition_to_idle(
    state_machines: dict[str, WorkerStateMachine],
    key: str,
    model_id: str,
) -> None:
    """Handle state machine transition to idle.

    Handles various current states: BUSY, ERROR, UNLOADING, or unexpected.

    Args:
        state_machines: Dict mapping normalized string keys to their state machines.
        key: The normalized string key for lookups.
        model_id: The original model ID string for logging.
    """
    if key not in state_machines:
        return

    sm = state_machines[key]
    current_state = sm.current_state

    if current_state == WorkerState.BUSY:
        success = sm.transition(WorkerState.LOADED, reason="inference_completed")
        if not success:
            _handle_failed_idle_transition(sm, key, model_id)
    elif current_state == WorkerState.ERROR:
        sm.force_idle(reason="error_recovery")
    elif current_state == WorkerState.UNLOADING:
        logger.info(f"Model {model_id} unloading, not forcing idle")
    elif current_state != WorkerState.LOADED:
        logger.warning(f"Model {model_id} in unexpected state {current_state.value}")
        sm.force_idle(reason=f"unexpected_state_{current_state.value}")


def _handle_failed_idle_transition(
    sm: WorkerStateMachine, key: str, model_id: str
) -> None:
    """Handle failed BUSY -> LOADED transition.

    Called when the normal transition fails, attempts recovery.
    """
    new_state = sm.current_state
    if new_state != WorkerState.BUSY:
        logger.warning(f"State changed for {model_id}: BUSY → {new_state.value}")
        if new_state == WorkerState.ERROR:
            sm.force_idle(reason="error_recovery_after_race")
        elif new_state != WorkerState.UNLOADING:
            sm.force_idle(reason="recovery_after_state_change")
    else:
        history = sm.get_transition_history(limit=3)
        hist_str = [f"{t.from_state.value}→{t.to_state.value}" for t in history]
        logger.warning(
            f"Transition BUSY → LOADED failed for {model_id}. History: {hist_str}"
        )
        sm.force_idle(reason="transition_failed")


async def update_model_idle_status_async(
    models: dict[str, ModelResourceInfo],
    key: str,
    model_id: str,
    event_bus: "EventBus",
) -> None:
    """Update inference timing fields when marking idle and emit event.

    SM transition (in transition_to_idle) already set the state to LOADED;
    status auto-derives. This function handles timing field cleanup and
    INFERENCE_COMPLETED event emission.

    Args:
        models: Dict mapping normalized string keys to their resource info.
        key: The normalized string key for lookups.
        model_id: The original model ID string for logging.
        event_bus: The EventBus instance for event emission.
    """
    if key not in models:
        return

    m = models[key]
    had_active_inference = m.current_inference_start is not None

    if had_active_inference:
        current_time = time.time()
        m.last_inference_end = current_time
        m.last_inference_time = current_time
        m.current_inference_start = None
        m.inference_state = None
        logger.info(f"✅ Model {model_id} marked idle")
    else:
        logger.debug(
            "Model %s idle bookkeeping ran without active inference", model_id
        )

    if not had_active_inference:
        return

    from .events import emit_inference_completed

    await emit_inference_completed(event_bus, model_id, m.last_inference_end)
