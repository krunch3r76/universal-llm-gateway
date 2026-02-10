"""
Event-driven model state waiter (load and unload).

Replaces polling with async event waiting for model state transitions.
Integrates with GatewayWebSocketClient callbacks for real-time notifications.

Uses full synthetic model IDs (with context length) as event keys to prevent
cross-variant wakeup bugs. Different context lengths are treated as separate models.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .gateway_hooks import register_gateway
from .handles import (
    LoadResult,
    LoadWaitHandle,
    UnloadResult,
    UnloadWaitHandle,
    _GatewayWsClient,
)
from .signals import notify_gateway_disconnected

if TYPE_CHECKING:
    from gateways import GatewayInstance

    from ..coordination import GlobalModelLoadCoordinator

logger = get_logger(__name__)


class ModelLoadWaiter:
    """
    Event-driven model load/unload waiter with GlobalModelLoadCoordinator integration.

    Responsibilities:
    1. Maintains registry of pending load AND unload operations
    2. Hooks into WebSocket callbacks (MODEL_LOADED, MODEL_UNLOADED)
    3. Notifies GlobalModelLoadCoordinator when events arrive
    4. Wakes waiters when load/unload completes

    Architecture (Event-Driven State Management):
    - WebSocket events drive ALL state updates
    - No polling loops for load or unload waiting
    - Coordinator state is always fresh and accurate

    Usage:
        waiter = ModelLoadWaiter(global_load_coordinator=coordinator)
        waiter.register_gateway(gateway)

        # Wait for load:
        result = await waiter.wait_for_load("gateway-1", "model-id", timeout=300)

        # Wait for unload:
        result = await waiter.wait_for_unload("gateway-1", "model-id", timeout=30)
    """

    def __init__(
        self,
        global_load_coordinator: GlobalModelLoadCoordinator | None = None,
        event_bus=None,
    ):
        self._global_load_coordinator = global_load_coordinator
        self._event_bus = event_bus
        self._pending_loads: dict[tuple[str, str], LoadWaitHandle] = {}
        self._pending_unloads: dict[tuple[str, str], UnloadWaitHandle] = {}
        self._registered_gateways: set[str] = set()
        self._ws_clients_by_gateway: dict[str, _GatewayWsClient] = {}
        # Gateway instances for HTTP fallback checks
        self._gateway_instances: dict[str, GatewayInstance] = {}

    def register_gateway(self, gateway: GatewayInstance) -> None:
        """
        Register a gateway to receive load/unload events from its WebSocket client.

        Hooks into MODEL_LOADED and MODEL_UNLOADED callbacks for real-time
        notification of state transitions.
        """
        register_gateway(
            gateway=gateway,
            registered_gateways=self._registered_gateways,
            ws_clients_by_gateway=self._ws_clients_by_gateway,
            gateway_instances=self._gateway_instances,
            pending_loads=self._pending_loads,
            pending_unloads=self._pending_unloads,
            global_load_coordinator=self._global_load_coordinator,
            to_event_key_func=self._to_event_key,
            event_bus=self._event_bus,
        )

    @property
    def pending_waiter_counts(self) -> tuple[int, int]:
        """Return (pending_loads, pending_unloads) for telemetry."""
        return len(self._pending_loads), len(self._pending_unloads)

    def stop(self) -> None:
        """Clean up pending waiters."""
        for handle in self._pending_loads.values():
            handle.set_failed("Waiter shutting down")
        for handle in self._pending_unloads.values():
            handle.set_unloaded()  # Treat shutdown as unloaded
        self._pending_loads.clear()
        self._pending_unloads.clear()
        self._registered_gateways.clear()
        logger.info("ModelLoadWaiter stopped")

    # =========================================================================
    # Load Waiting
    # =========================================================================

    async def wait_for_load(
        self,
        gateway_name: str,
        model_id: ModelId,
        timeout: float = 300.0,
    ) -> LoadResult:
        """
        Wait for model to finish loading on gateway.

        Uses hybrid approach: short event waits with periodic status checks.
        This prevents indefinite stalls if WebSocket events are missed.
        """
        event_check_interval = 5.0  # Check every 5 seconds

        event_key = self._to_event_key(model_id)

        if not self._is_gateway_ws_connected(gateway_name):
            logger.warning(
                f"Gateway {gateway_name} WebSocket not connected; "
                f"cannot wait for load of {event_key}"
            )
            return LoadResult.GATEWAY_UNREACHABLE

        # Fast path: already loaded
        if self._is_event_key_loaded_on_gateway(gateway_name, event_key):
            logger.debug(f"Model {event_key} already loaded on {gateway_name}")
            return LoadResult.LOADED

        key = (gateway_name, event_key)

        handle = self._pending_loads.get(key)
        if handle is None:
            handle = LoadWaitHandle(gateway_name=gateway_name, model_id=event_key)
            self._pending_loads[key] = handle
            logger.debug(
                f"Created load wait handle for {model_id} → {event_key} "
                f"on {gateway_name}"
            )
        handle.waiter_count += 1

        logger.info(
            f"⏳ Waiting for {event_key} load on {gateway_name} "
            f"(hybrid: {event_check_interval}s intervals, timeout={timeout}s)"
        )

        elapsed = 0.0
        try:
            while elapsed < timeout:
                # Calculate wait time before try block to avoid unbound warning
                wait_time = min(event_check_interval, timeout - elapsed)
                try:
                    # Wait for event with short timeout
                    await asyncio.wait_for(handle.event.wait(), timeout=wait_time)

                    # Event received!
                    result = handle.result
                    logger.info(
                        f"✅ Load wait completed for {event_key} on {gateway_name}: "
                        f"{result.value} (via event)"
                    )
                    return result

                except TimeoutError:
                    elapsed += wait_time

                    # Fallback 1: check WebSocket cache
                    if self._is_event_key_loaded_on_gateway(gateway_name, event_key):
                        logger.warning(
                            f"⚠️ MODEL_LOADED event missed but {event_key} confirmed "
                            f"loaded on {gateway_name} via WebSocket cache "
                            f"(elapsed={elapsed:.0f}s)"
                        )
                        return LoadResult.LOADED

                    # Fallback 2: HTTP status check (handles stale WebSocket cache)
                    from .status_check import check_model_loaded_via_http

                    gateway = self._gateway_instances.get(gateway_name)
                    http_loaded = await check_model_loaded_via_http(
                        gateway, model_id, gateway_name
                    )
                    if http_loaded:
                        logger.warning(
                            f"⚠️ MODEL_LOADED event missed but {event_key} confirmed "
                            f"loaded on {gateway_name} via HTTP status check "
                            f"(elapsed={elapsed:.0f}s)"
                        )
                        # Update coordinator state since WebSocket missed the event
                        if self._global_load_coordinator:
                            self._global_load_coordinator.on_model_loaded_event(
                                str(model_id), gateway_name
                            )
                        return LoadResult.LOADED

                    # Check if gateway still connected
                    if not self._is_gateway_ws_connected(gateway_name):
                        logger.warning(
                            f"Gateway {gateway_name} disconnected during wait"
                        )
                        return LoadResult.GATEWAY_UNREACHABLE

                    # Check if load failed (event might have been received)
                    if handle.event.is_set():
                        return handle.result

                    logger.debug(
                        f"Still waiting for {event_key} on {gateway_name} "
                        f"({elapsed:.0f}s elapsed)"
                    )

            # Final timeout - do one last status check
            if self._is_event_key_loaded_on_gateway(gateway_name, event_key):
                logger.warning(
                    f"⚠️ MODEL_LOADED event missed but {event_key} confirmed "
                    f"loaded on {gateway_name} at final check"
                )
                return LoadResult.LOADED

            logger.warning(
                f"⏰ Timeout waiting for {event_key} load on {gateway_name} "
                f"after {timeout}s"
            )
            return LoadResult.TIMEOUT

        finally:
            handle.waiter_count -= 1
            if handle.waiter_count <= 0:
                self._pending_loads.pop(key, None)

    # =========================================================================
    # Unload Waiting
    # =========================================================================

    async def wait_for_unload(
        self,
        gateway_name: str,
        model_id: ModelId,
        timeout: float = 30.0,
    ) -> UnloadResult:
        """
        Wait for model to finish unloading on gateway.

        Uses asyncio.Event - no polling. Critical for VRAM release timing.

        Args:
            gateway_name: Gateway identifier
            model_id: Model being unloaded (canonical)
            timeout: Maximum seconds to wait (default 30s - unloads are fast)

        Returns:
            UnloadResult indicating outcome
        """
        if not self._is_gateway_ws_connected(gateway_name):
            logger.warning(
                f"Gateway {gateway_name} WebSocket not connected; "
                f"cannot wait for unload of {model_id}"
            )
            return UnloadResult.GATEWAY_UNREACHABLE

        event_key = self._to_event_key(model_id)
        key = (gateway_name, event_key)

        handle = self._pending_unloads.get(key)
        if handle is None:
            handle = UnloadWaitHandle(gateway_name=gateway_name, model_id=event_key)
            self._pending_unloads[key] = handle
            logger.debug(
                f"Created unload wait handle for {model_id} → {event_key} "
                f"on {gateway_name}"
            )
        handle.waiter_count += 1

        logger.info(
            f"⏳ Waiting for {event_key} unload on {gateway_name} "
            f"(event-driven, timeout={timeout}s, waiters={handle.waiter_count})"
        )

        try:
            await asyncio.wait_for(handle.event.wait(), timeout=timeout)
            result = handle.result
            logger.info(
                f"✅ Unload wait completed for {event_key} on {gateway_name}: "
                f"{result.value}"
            )
            return result
        except TimeoutError:
            logger.warning(
                f"⏰ Timeout waiting for {event_key} unload on {gateway_name} "
                f"after {timeout}s"
            )
            return UnloadResult.TIMEOUT
        finally:
            handle.waiter_count -= 1
            if handle.waiter_count <= 0:
                self._pending_unloads.pop(key, None)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def notify_gateway_disconnected(self, gateway_name: str) -> None:
        """Handle gateway WebSocket disconnect - fail all pending waits."""
        notify_gateway_disconnected(
            self._pending_loads,
            self._pending_unloads,
            gateway_name,
        )

    def _is_gateway_ws_connected(self, gateway_name: str) -> bool:
        """
        Predicate: should we do event-driven waiting for this gateway?

        Invariant: event-driven waits require an active gateway WebSocket.
        If ws is down, waiting cannot succeed and should fail fast.
        """
        ws_client = self._ws_clients_by_gateway.get(gateway_name)
        if ws_client is None:
            return False
        return ws_client.is_connected

    def _is_event_key_loaded_on_gateway(
        self, gateway_name: str, event_key: str
    ) -> bool:
        """
        Predicate: event_key is currently loaded on gateway.

        Uses gateway WebSocket cached state (instant, event-driven).
        This prevents waits from timing out when the model was already loaded
        before the waiter was registered (e.g., Stargate restart).
        """
        ws_client = self._ws_clients_by_gateway.get(gateway_name)
        if ws_client is None or not ws_client.is_connected:
            return False

        for loaded_model_id_str in ws_client.get_loaded_models():
            # Parse at boundary: WebSocket cache returns strings, convert to ModelId
            loaded_model_id = ModelId.parse(loaded_model_id_str)
            if self._to_event_key(loaded_model_id) == event_key:
                return True
        return False

    def _to_event_key(self, model_id: ModelId) -> str:
        """Get routing key for event matching."""
        return model_id.routing_key

    # =========================================================================
    # Manual Signals (for non-event-driven paths)
    # =========================================================================

    def signal_loaded(self, gateway_name: str, model_id: ModelId) -> None:
        """Manually signal that a model is loaded."""
        event_key = self._to_event_key(model_id)
        key = (gateway_name, event_key)
        handle = self._pending_loads.get(key)
        if handle:
            handle.set_loaded()

    def signal_failed(
        self, gateway_name: str, model_id: ModelId, error: str | None = None
    ) -> None:
        """Manually signal that a load failed."""
        event_key = self._to_event_key(model_id)
        key = (gateway_name, event_key)
        handle = self._pending_loads.get(key)
        if handle:
            handle.set_failed(error)

    def signal_unloaded(self, gateway_name: str, model_id: ModelId) -> None:
        """Manually signal that a model is unloaded."""
        event_key = self._to_event_key(model_id)
        key = (gateway_name, event_key)
        handle = self._pending_unloads.get(key)
        if handle:
            handle.set_unloaded()
