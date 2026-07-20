"""Shared resource-tracker and engine subprocess state queries for reconciliation."""

from __future__ import annotations

import os

from universal_logging import get_logger

logger = get_logger(__name__)


class TrackerStateMixin:
    """Tracker status, transition filters, and engine PID liveness helpers."""

    @staticmethod
    def _is_engine_pid_alive(pid: int) -> bool:
        """Check engine subprocess liveness via signal 0 (no RPC needed)."""
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            logger.error(
                "Permission denied when checking PID %d liveness. Assuming dead or unmanageable.",
                pid,
            )
            return False
        except OSError:
            return False

    def _get_tracker_status(self, model_id: str) -> str | None:
        info = self._resource_tracker._models.get(model_id)
        if info and info.status:
            return info.status.value
        return None

    def _get_transitioning_models(self) -> set[str]:
        """Return model IDs currently in LOADING or UNLOADING state.

        These have legitimate worker processes and must not be treated as phantoms.
        """
        from ..types import ModelStatus

        return {
            model_id
            for model_id, info in self._resource_tracker._models.items()
            if info.status in (ModelStatus.LOADING, ModelStatus.UNLOADING)
        }

    def _get_tracked_vram(self, model_id: str) -> int:
        info = self._resource_tracker._models.get(model_id)
        if info is None:
            return 0
        return info.effective_vram_mb
