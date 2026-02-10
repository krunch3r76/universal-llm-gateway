"""Process monitor utilities for worker processes."""

from universal_logging import get_logger

from ..process.state import ProcessState

logger = get_logger(__name__)


class ProcessMonitor:
    """
    Provides resource monitoring for worker processes.

    Offers resource usage queries and peak usage tracking for model workers.
    """

    def __init__(self, process_state: ProcessState):
        """
        Initialize process monitor.

        Args:
            process_state: ProcessState containing supervisor references.
        """
        self._process_state = process_state

    async def get_resource_usage(self, model_id: str):
        """
        Get current resource usage for a model's worker.

        Args:
            model_id: Model ID to get resource usage for.

        Returns:
            ProcessResourceUsage object or None if not available.
        """
        supervisor = self._process_state.get_supervisor(model_id)
        if not supervisor:
            return None

        try:
            return await supervisor.get_resource_usage()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to get resource usage for %s: %s", model_id, exc)
            return None

    def get_peak_usage(self, model_id: str):
        """
        Get peak resource usage for a model's worker.

        Args:
            model_id: Model ID to get peak usage for.

        Returns:
            Dict with peak usage data or None if not available.
        """
        supervisor = self._process_state.get_supervisor(model_id)
        if not supervisor:
            return None

        try:
            return supervisor.get_peak_usage()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to get peak usage for %s: %s", model_id, exc)
            return None

    def reset_peak_usage(self, model_id: str):
        """
        Reset peak resource usage tracking for a model's worker.

        Args:
            model_id: Model ID to reset peak usage for.
        """
        supervisor = self._process_state.get_supervisor(model_id)
        if not supervisor:
            return

        try:
            supervisor.reset_peak_usage()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to reset peak usage for %s: %s", model_id, exc)
