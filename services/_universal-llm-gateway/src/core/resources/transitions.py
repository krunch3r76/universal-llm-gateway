"""State transition helpers for model lifecycle management.

Provides functions for handling state machine transitions during model
loading, idle, and error recovery operations.
"""

import time

from universal_logging import get_logger

from src.core.workers.state_machine import WorkerState, WorkerStateMachine

from .types import ModelResourceInfo, ModelStatus

logger = get_logger(__name__)


def handle_error_state_recovery(
    state_machines: dict[str, WorkerStateMachine],
    models: dict[str, ModelResourceInfo],
    key: str,
    model_id: str,
) -> None:
    """Clear ERROR state if present to allow retry.

    When a model is in ERROR state, this function resets it to NOT_LOADED
    so that a new load attempt can be made.

    Args:
        state_machines: Dict mapping normalized string keys to their state machines.
        models: Dict mapping normalized string keys to their resource info.
        key: The normalized string key for lookups.
        model_id: The original model ID string for logging.
    """
    if key not in models:
        return

    current_status = models[key].status
    if current_status != ModelStatus.ERROR:
        return

    error_msg = models[key].error_message or "unknown"
    logger.info(
        f"Clearing ERROR state for {model_id} (previous: {error_msg}) to allow retry"
    )

    if key in state_machines:
        success = state_machines[key].clear_error("Retry load attempt")
        if success:
            logger.debug(f"Cleared error state for {model_id}")
            models[key].status = ModelStatus.NOT_LOADED
            models[key].error_message = None
        else:
            logger.warning(f"Failed to clear error state for {model_id}")
    else:
        models[key].error_message = None
        models[key].status = ModelStatus.NOT_LOADED


def transition_to_loading(
    state_machines: dict[str, WorkerStateMachine],
    key: str,
    model_id: str,
    set_status_callback,
) -> None:
    """Transition model to LOADING state.

    Args:
        state_machines: Dict mapping normalized string keys to their state machines.
        key: The normalized string key for lookups.
        model_id: The original model ID string for logging.
        set_status_callback: Callback to set model status (model_id, status).
    """
    if key in state_machines:
        success = state_machines[key].transition(
            WorkerState.LOADING, reason="model_loading_started"
        )
        if success:
            set_status_callback(model_id, ModelStatus.LOADING)
        else:
            logger.error(f"Failed to transition {model_id} to LOADING")
    else:
        logger.warning(f"No state machine for {model_id}")
        set_status_callback(model_id, ModelStatus.LOADING)


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
    event_bus,
) -> None:
    """Update model status fields when marking idle.

    Directly emits INFERENCE_COMPLETED event via EventBus.

    Args:
        models: Dict mapping normalized string keys to their resource info.
        key: The normalized string key for lookups.
        model_id: The original model ID string for logging.
        event_bus: EventBus instance for event emission.
    """
    if key not in models:
        return

    if models[key].status == ModelStatus.BUSY:
        models[key].status = ModelStatus.LOADED
        current_time = time.time()
        models[key].last_inference_end = current_time
        models[key].last_inference_time = current_time
        models[key].current_inference_start = None
        models[key].inference_state = None
        logger.info(f"✅ Model {model_id} marked idle")
    elif models[key].status != ModelStatus.LOADED:
        models[key].status = ModelStatus.LOADED
        logger.debug(f"Model {model_id} status updated to LOADED")

    models[key].last_updated = time.time()

    # Emit event with timestamp (fire-and-forget)
    from .events import emit_inference_completed

    last_inference_time = models[key].last_inference_end or time.time()
    if models[key].last_inference_end is None:
        logger.warning(
            f"MODEL_IDLE emitted for {model_id} without "
            "pre-recorded last_inference_end; falling back to current time"
        )

    await emit_inference_completed(event_bus, model_id, last_inference_time)
