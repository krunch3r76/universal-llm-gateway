"""
Event subscription wiring for batch routing components.

Connects BatchModelTracker and DynamicBudgetManager to event bus.

Domain: Proxy

Events (from src/scheduling/events.py):
- MODEL_LOADED ("ModelLoaded"): Release model claim, invalidate budget
- MODEL_UNLOADED ("ModelUnloaded"): Release model claim, invalidate budget
- MODEL_LOADING_FAILED ("ModelLoadingFailed"): Release model claim
- GATEWAY_RESOURCE_UPDATE ("GatewayResourceUpdate"): Invalidate budget

CRITICAL: Always import signal constants from src/scheduling/events.py
         DO NOT use hardcoded strings - they won't match!

Note: BatchModelTracker methods are synchronous (lock-free design per ADR-1),
so event handlers are also synchronous - no asyncio.create_task needed.

Startup Race Note (from review):
- Events fired before subscription setup will be missed
- This is ACCEPTABLE because:
  - BatchModelTracker starts empty (no stale claims)
  - DynamicBudgetManager cache starts empty (will refresh on first use)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

# CRITICAL: Import signal constants - DO NOT hardcode strings
from src.scheduling.events import (
    GATEWAY_RESOURCE_UPDATE,
    MODEL_LOADED,
    MODEL_LOADING_FAILED,
    MODEL_UNLOADED,
)

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from .budget_manager import DynamicBudgetManager
    from .model_tracker import BatchModelTracker

logger = get_logger(__name__)


def setup_batch_routing_event_subscriptions(
    event_bus: EventBus,
    model_tracker: BatchModelTracker | None = None,
    budget_manager: DynamicBudgetManager | None = None,
) -> None:
    """
    Wire batch routing components to event bus.

    Call this during application startup after creating components.

    Args:
        event_bus: Event bus instance
        model_tracker: Optional model tracker to wire
        budget_manager: Optional budget manager to wire
    """
    if model_tracker:
        _setup_model_tracker_subscriptions(event_bus, model_tracker)

    if budget_manager:
        _setup_budget_manager_subscriptions(event_bus, budget_manager)

    logger.info(
        f"Batch routing event subscriptions configured: "
        f"model_tracker={model_tracker is not None}, "
        f"budget_manager={budget_manager is not None}"
    )


def _setup_model_tracker_subscriptions(
    event_bus: EventBus,
    model_tracker: BatchModelTracker,
) -> None:
    """Set up model tracker event subscriptions."""

    def on_model_loaded(event) -> None:
        """Release claim when model finishes loading."""
        model_id = event.payload.get("model_id")
        if model_id:
            model_tracker.release_model_load(model_id)  # Synchronous (lock-free)
            logger.debug(f"Released model claim on MODEL_LOADED: {model_id}")

    def on_model_unloaded(event) -> None:
        """Release claim when model unloaded (safety net)."""
        model_id = event.payload.get("model_id")
        if model_id:
            model_tracker.release_model_load(model_id)  # Synchronous (lock-free)
            logger.debug(f"Released model claim on MODEL_UNLOADED: {model_id}")

    def on_model_load_failed(event) -> None:
        """Release claim when load fails."""
        model_id = event.payload.get("model_id")
        if model_id:
            model_tracker.release_model_load(model_id)  # Synchronous (lock-free)
            logger.debug(f"Released model claim on MODEL_LOADING_FAILED: {model_id}")

    # Subscribe using IMPORTED CONSTANTS (not hardcoded strings!)
    # Signal values: MODEL_LOADED="ModelLoaded", MODEL_UNLOADED="ModelUnloaded", etc.
    event_bus.subscribe_async(MODEL_LOADED, on_model_loaded)
    event_bus.subscribe_async(MODEL_UNLOADED, on_model_unloaded)
    event_bus.subscribe_async(MODEL_LOADING_FAILED, on_model_load_failed)

    logger.debug(
        f"Model tracker subscribed to: {MODEL_LOADED}, {MODEL_UNLOADED}, "
        f"{MODEL_LOADING_FAILED}"
    )


def _setup_budget_manager_subscriptions(
    event_bus: EventBus,
    budget_manager: DynamicBudgetManager,
) -> None:
    """Set up budget manager event subscriptions."""

    def on_model_state_changed(event) -> None:
        """Invalidate budget cache on model state changes."""
        budget_manager.invalidate()
        logger.debug("Budget cache invalidated on model state change")

    # Subscribe using IMPORTED CONSTANTS
    event_bus.subscribe_async(MODEL_LOADED, on_model_state_changed)
    event_bus.subscribe_async(MODEL_UNLOADED, on_model_state_changed)
    event_bus.subscribe_async(GATEWAY_RESOURCE_UPDATE, on_model_state_changed)

    logger.debug(
        f"Budget manager subscribed to: {MODEL_LOADED}, {MODEL_UNLOADED}, "
        f"{GATEWAY_RESOURCE_UPDATE}"
    )
