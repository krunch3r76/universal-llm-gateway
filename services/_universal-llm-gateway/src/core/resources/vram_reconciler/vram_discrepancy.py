"""Bidirectional VRAM accounting discrepancy detection and event emission."""

from __future__ import annotations

from universal_logging import get_logger

from ..hardware import get_vram_info
from .constants import VRAM_DISCREPANCY_THRESHOLD_MB

logger = get_logger(__name__)


class VramDiscrepancyMixin:
    """Compare hardware VRAM usage against tracked catalog totals."""

    async def _check_vram_discrepancy(self, tracked_models: set[str]) -> None:
        hw = get_vram_info()
        hardware_used = hw["total_vram_mb"] - hw["available_vram_mb"]

        tracked_used = 0
        for model_id in tracked_models:
            info = self._resource_tracker._models.get(model_id)
            if info is not None:
                tracked_used += (
                    info.measured_vram_mb
                    if info.measured_vram_mb is not None
                    else info.vram_usage_mb
                )

        discrepancy = hardware_used - tracked_used

        if discrepancy > VRAM_DISCREPANCY_THRESHOLD_MB:
            logger.warning(
                "VRAM orphan detected: hardware=%sMB tracked=%sMB delta=+%sMB "
                "(unmanaged GPU processes likely)",
                hardware_used,
                tracked_used,
                discrepancy,
            )
            await self._emit_vram_orphan(
                hardware_used=hardware_used,
                tracked_used=tracked_used,
                discrepancy=discrepancy,
                tracked_models=sorted(tracked_models),
            )
            unmanaged = await self._scan_gpu_processes()
            if unmanaged:
                logger.error("Unmanaged GPU processes detected: %s", unmanaged)
        elif discrepancy < -VRAM_DISCREPANCY_THRESHOLD_MB:
            logger.warning(
                "VRAM staleness detected: tracked=%sMB but hardware only=%sMB "
                "(delta=%sMB — catalog profiles stale)",
                tracked_used,
                hardware_used,
                discrepancy,
            )
            await self._emit_vram_staleness(
                hardware_used=hardware_used,
                tracked_used=tracked_used,
                discrepancy=discrepancy,
                tracked_models=sorted(tracked_models),
            )

    async def _emit_vram_orphan(
        self,
        hardware_used: int,
        tracked_used: int,
        discrepancy: int,
        tracked_models: list[str],
    ) -> None:
        if self._event_bus is None:
            return
        try:
            from ...events.types import VramOrphanDetected

            await self._event_bus.publish_nowait(
                VramOrphanDetected(
                    hardware_used_mb=hardware_used,
                    catalog_used_mb=tracked_used,
                    discrepancy_mb=discrepancy,
                    tracked_models=tracked_models,
                )
            )
        except Exception:
            logger.error("Failed to publish VRAM orphan event", exc_info=True)

    async def _emit_vram_staleness(
        self,
        hardware_used: int,
        tracked_used: int,
        discrepancy: int,
        tracked_models: list[str],
    ) -> None:
        if self._event_bus is None:
            return
        try:
            from ...events.types import VramStalenessDetected

            await self._event_bus.publish_nowait(
                VramStalenessDetected(
                    hardware_used_mb=hardware_used,
                    catalog_used_mb=tracked_used,
                    discrepancy_mb=discrepancy,
                    tracked_models=tracked_models,
                )
            )
        except Exception:
            logger.error("Failed to publish VRAM staleness event", exc_info=True)
