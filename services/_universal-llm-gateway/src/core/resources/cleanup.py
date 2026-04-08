"""Cleanup operations for resource tracking.

Handles stale model cleanup and resource reclamation.
Thread Safety: Not needed. All calls from async event loop.
"""

import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

from .hardware import pid_exists
from .types import ModelStatus

if TYPE_CHECKING:
    from .tracker import ResourceTracker

logger = get_logger(__name__)

STALE_THRESHOLD_SECONDS = 300


def cleanup_stale_models(tracker: "ResourceTracker") -> list[str]:
    """Clean up stale model entries.

    Removes models that:
    - Haven't been updated within the stale threshold
    - Don't have a running process
    - Are stuck in LOADING or UNLOADING state

    Returns:
        List of model IDs that were cleaned up.
    """
    cleaned_up = []
    current_time = time.time()

    stale_models = []
    for model_id, info in tracker._models.items():
        if (current_time - info.last_updated) > STALE_THRESHOLD_SECONDS:
            # Skip if process is still running
            if info.process_pid and pid_exists(info.process_pid):
                continue
            # Only clean up stuck operations
            if info.status in [ModelStatus.LOADING, ModelStatus.UNLOADING]:
                stale_models.append(model_id)

    for model_id in stale_models:
        if model_id in tracker._models:
            del tracker._models[model_id]
            tracker._state_machines.pop(model_id, None)
            tracker._variant_registry.unregister(model_id)
            cleaned_up.append(model_id)
            logger.info(f"Cleaned up stale model: {model_id}")

    return cleaned_up
