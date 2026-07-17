"""Event bus integration for Gateway WebSocket client.

Publishes GATEWAY_STATE_CHANGED and model.execution.completed events.
"""

import asyncio
from collections.abc import Callable
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

    def __init__(
        self,
        ws_url: str,
        gateway_name: str,
        event_bus: Any = None,
        gateway_state_snapshot_provider: Callable[[], dict | None] | None = None,
    ) -> None:
        self._ws_url = ws_url
        self._gateway_name = gateway_name
        self._event_bus = event_bus
        self._previous_connected: bool | None = None
        self._gateway_state_snapshot_provider = gateway_state_snapshot_provider

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

        await self._event_bus.publish_nowait(
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
            from src.scheduling.events import FederationCatalogVramDrift

            await self._event_bus.publish_nowait(
                FederationCatalogVramDrift(
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

            await self._event_bus.publish_nowait(
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

    def schedule_model_loading_started(self, model_id: str) -> None:
        """Schedule model.loading.started event emission (non-blocking).

        Coordination signal: batch pipelines (e.g. RAG contextualization)
        subscribe to anticipate the cold-load window.

        Emitted directly from the MODEL_LOADING_STARTED message handler so
        emission is decoupled from the lifecycle-callback chain (which is
        overwritten by federation/manager wiring after registration).
        """
        if self._event_bus is None:
            return
        asyncio.create_task(
            self._publish_model_loading_started(model_id),
            name=f"model_loading_started_{model_id}",
        )

    async def _publish_model_loading_started(self, model_id: str) -> None:
        try:
            from src.scheduling.events import ModelLoadingStarted

            await self._event_bus.publish_nowait(
                ModelLoadingStarted(
                    url=ws_url_to_http(self._ws_url),
                    model_id=model_id,
                )
            )
            logger.debug(
                f"Emitted MODEL_LOADING_STARTED for {model_id} on {self._gateway_name}"
            )
        except Exception as e:
            logger.warning(f"Failed to emit model.loading.started for {model_id}: {e}")

    def schedule_model_loading_progress(
        self, model_id: str, phase: str, pct: int | float
    ) -> None:
        """Schedule model.loading.progress heartbeat emission (non-blocking)."""
        if self._event_bus is None:
            return
        asyncio.create_task(
            self._publish_model_loading_progress(model_id, phase, pct),
            name=f"model_loading_progress_{model_id}",
        )

    async def _publish_model_loading_progress(
        self, model_id: str, phase: str, pct: int | float
    ) -> None:
        try:
            from src.scheduling.events import ModelLoadingProgress

            await self._event_bus.publish_nowait(
                ModelLoadingProgress(
                    url=ws_url_to_http(self._ws_url),
                    model_id=model_id,
                    phase=phase,
                    pct=pct,
                    gateway_name=self._gateway_name,
                )
            )
            logger.debug(
                "Emitted MODEL_LOADING_PROGRESS for %s on %s phase=%s pct=%s",
                model_id,
                self._gateway_name,
                phase,
                pct,
            )
        except Exception as e:
            logger.warning(
                f"Failed to emit model.loading.progress for {model_id}: {e}"
            )

    def schedule_model_loaded(
        self,
        model_id: str,
        vram_mb: int = 0,
        ram_mb: int = 0,
    ) -> None:
        """Schedule model.loaded event emission (non-blocking).

        Coordination signal: batch pipelines resume submissions when the
        cold-load window closes.

        Emitted directly from the MODEL_LOADED message handler (see
        schedule_model_loading_started for rationale).
        """
        if self._event_bus is None:
            return
        asyncio.create_task(
            self._publish_model_loaded(model_id, vram_mb, ram_mb),
            name=f"model_loaded_{model_id}",
        )

    async def _publish_model_loaded(
        self,
        model_id: str,
        vram_mb: int,
        ram_mb: int,
    ) -> None:
        try:
            from src.scheduling.events import ModelLoaded

            await self._event_bus.publish_nowait(
                ModelLoaded(
                    url=ws_url_to_http(self._ws_url),
                    model_id=model_id,
                    gateway_name=self._gateway_name,
                    vram_mb=vram_mb,
                    ram_mb=ram_mb,
                )
            )
            logger.debug(f"Emitted MODEL_LOADED for {model_id} on {self._gateway_name}")
        except Exception as e:
            logger.warning(f"Failed to emit model.loaded for {model_id}: {e}")

    def schedule_model_load_failed(
        self,
        model_id: str,
        error: str,
        worker_snapshot: dict | None = None,
        gateway_state_snapshot: dict | None = None,
    ) -> None:
        """Schedule model.load.failed event emission (non-blocking).

        Coordination signal: batch pipelines restore optimism — the next
        submission will retry and surface the failure loudly.

        Both snapshots are caller-provided. The MODEL_LOAD_FAILED handler
        captures gateway_state_snapshot once via capture_gateway_state_snapshot()
        and passes it to both this scheduler and the federation callback so
        local emission and federation forwarding observe identical state.
        """
        if self._event_bus is None:
            return
        asyncio.create_task(
            self._publish_model_load_failed(
                model_id, error, gateway_state_snapshot, worker_snapshot
            ),
            name=f"model_load_failed_{model_id}",
        )

    def capture_gateway_state_snapshot(self) -> dict | None:
        """Capture master-side snapshot from cached GatewayState (best-effort).

        Public so the MODEL_LOAD_FAILED handler can capture once and fan out
        to both event bus emission and federation forwarding without double-
        snapshotting (GatewayState mutates rapidly under telemetry).
        """
        if self._gateway_state_snapshot_provider is None:
            return None
        try:
            return self._gateway_state_snapshot_provider()
        except Exception as e:
            logger.warning(
                "gateway_state_snapshot capture failed for %s: %s",
                self._gateway_name,
                e,
            )
            return None

    async def _publish_model_load_failed(
        self,
        model_id: str,
        error: str,
        gateway_state_snapshot: dict | None,
        worker_snapshot: dict | None,
    ) -> None:
        try:
            from src.scheduling.events import ModelLoadingFailed

            await self._event_bus.publish_nowait(
                ModelLoadingFailed(
                    url=ws_url_to_http(self._ws_url),
                    model_id=model_id,
                    error=error,
                    gateway_name=self._gateway_name,
                    gateway_state_snapshot=gateway_state_snapshot,
                    worker_snapshot=worker_snapshot,
                )
            )
            logger.debug(
                f"Emitted MODEL_LOAD_FAILED for {model_id} on {self._gateway_name}"
            )
        except Exception as e:
            logger.warning(f"Failed to emit model.load.failed for {model_id}: {e}")
