"""Event bus integration for Gateway WebSocket client.

Publishes GATEWAY_STATE_CHANGED and model.execution.completed events.
"""

import asyncio
from typing import Any

from universal_logging import get_logger

from ..event import compute_state_transition, ws_url_to_http

logger = get_logger(__name__)


class EventPublisher:
    """
    Publishes Gateway lifecycle events to event bus.

    Event-driven architecture: connection state changes trigger events.
    Consumers (routing, metrics, monitoring) react to these events.
    """

    def __init__(self, ws_url: str, gateway_name: str, event_bus: Any = None) -> None:
        self._ws_url = ws_url
        self._gateway_name = gateway_name
        self._event_bus = event_bus
        self._previous_connected: bool | None = None

    async def emit_gateway_state_changed(self, connected: bool) -> None:
        """
        Emit GATEWAY_STATE_CHANGED event on connection state transitions.

        Called directly from connection callbacks - no polling required.
        Consumers (routing, metrics, monitoring) react to these events.

        Args:
            connected: Current connection state (True=connected, False=disconnected)
        """
        if self._event_bus is None:
            return

        transition = compute_state_transition(
            connected=connected,
            previous_connected=self._previous_connected,
            gateway_http_url=ws_url_to_http(self._ws_url),
            gateway_name=self._gateway_name,
        )

        if transition is None:
            return  # No actual transition

        self._previous_connected = connected

        from src.scheduling.events import GatewayStateChanged

        await self._event_bus.publish_async_nowait(
            GatewayStateChanged(
                url=transition.url,
                connectivity=transition.connectivity,
                health=transition.health,
                previous_connectivity=transition.previous_connectivity,
                previous_health=transition.previous_health,
                transition_type=transition.transition_type,
                check_duration_ms=transition.check_duration_ms,
            )
        )

        logger.debug(
            f"Emitted GATEWAY_STATE_CHANGED for {self._gateway_name}: "
            f"{transition.transition_type} -> "
            f"{'connected' if connected else 'disconnected'}"
        )

    def schedule_vram_drift(
        self,
        model_id: str,
        measured_mb: int,
        catalog_mb: int,
        drift_pct: float,
    ) -> None:
        """Schedule federation.catalog.vram.drift event emission (non-blocking)."""
        if self._event_bus is None:
            return
        asyncio.create_task(
            self._publish_vram_drift(model_id, measured_mb, catalog_mb, drift_pct),
            name=f"vram_drift_{model_id}",
        )

    async def _publish_vram_drift(
        self,
        model_id: str,
        measured_mb: int,
        catalog_mb: int,
        drift_pct: float,
    ) -> None:
        try:
            from src.scheduling.events import create_catalog_vram_drift_event

            await self._event_bus.publish_async_nowait(
                create_catalog_vram_drift_event(
                    gateway_id=self._gateway_name,
                    model_id=model_id,
                    measured_mb=measured_mb,
                    catalog_mb=catalog_mb,
                    drift_pct=drift_pct,
                )
            )
        except Exception as e:
            logger.warning(
                f"Failed to emit federation.catalog.vram.drift for {model_id}: {e}"
            )

    def schedule_capacity_freed(self, model_id: str) -> None:
        """
        Schedule model.capacity.freed event emission (non-blocking).

        Wake-only signal: capacity likely increased on this model.
        NOT a slot-release signal - emitted when Gateway reports idle/unloaded.

        Non-blocking: Schedules background task; does not await.

        Invariant: ∀ MODEL_IDLE or MODEL_UNLOADED, this method schedules emission

        Args:
            model_id: Model with freed capacity
        """
        if self._event_bus is None:
            return

        asyncio.create_task(
            self._publish_capacity_freed(model_id),
            name=f"capacity_freed_{model_id}",
        )

    async def _publish_capacity_freed(self, model_id: str) -> None:
        """
        Publish model.capacity.freed event (background task).

        Args:
            model_id: Model with freed capacity
        """
        try:
            from src.scheduling.events import ModelCapacityFreed

            await self._event_bus.publish_async_nowait(
                ModelCapacityFreed(
                    url=ws_url_to_http(self._ws_url),
                    model_id=model_id,
                )
            )
            logger.debug(
                f"🔔 Emitted model.capacity.freed for {model_id} "
                f"on {self._gateway_name} (waking queue)"
            )
        except Exception as e:
            logger.warning(
                f"Failed to emit model.capacity.freed for {model_id}: {e}",
                exc_info=True,
            )
