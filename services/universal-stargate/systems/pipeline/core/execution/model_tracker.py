"""
Model usage tracking for DAG execution.

Tracks which models are in use by which steps to prevent concurrent usage
of the same model by multiple steps.
"""

from universal_logging import get_logger

logger = get_logger(__name__)


class ModelUsageTracker:
    """
    Track which models are in use by which steps.

    Architectural pattern: Defer task creation instead of using locks/semaphores.
    """

    def __init__(self):
        self._active: dict[str, str] = {}  # model_id → step_id currently using it

    def can_acquire(self, model_id: str | None) -> bool:
        """
        Check if model is available (or no model coordination needed).

        Returns:
            True if model is available or no model is needed (None)
        """
        return model_id is None or model_id not in self._active

    def acquire(self, model_id: str | None, step_id: str) -> None:
        """Mark model as in use by step."""
        if model_id:
            self._active[model_id] = step_id

    def release(self, model_id: str | None, step_id: str) -> None:
        """Release model if currently held by this step."""
        if model_id and self._active.get(model_id) == step_id:
            del self._active[model_id]

    def get_blocking_step(self, model_id: str) -> str | None:
        """Get the step_id that is currently blocking this model."""
        return self._active.get(model_id)
