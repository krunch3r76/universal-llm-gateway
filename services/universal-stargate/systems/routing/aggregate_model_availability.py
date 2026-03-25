"""Aggregate model availability emitter for Stargate routing union.

Compares successive unions from ``get_all_available_models`` (local gateway plus
federation telemetry) and publishes coordination events when a watched model_id
crosses between unreachable and reachable in that aggregate view. This is
intentionally not the same as resident ``model.loaded`` state on a single URL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from universal_logging import get_logger

from systems.routing.selection.catalog import get_all_available_models

if TYPE_CHECKING:
    from gateways import SingleGatewayManager
    from systems.federation import FederatedGatewayManager
    from systems.proxy.stargate_core import StargateProxy

logger = get_logger(__name__)


class _EventBusPublish(Protocol):
    async def publish_async_nowait(self, event: Any) -> None: ...


class AggregateModelAvailabilityEmitter:
    """Track watched model IDs and emit aggregate availability transitions.

    The emitter stores the last computed union catalog and, on each reconcile,
    diffs the new union against the previous one. For every model_id in the
    watch set, it emits ``model.available`` when membership flips false→true and
    ``model.unavailable`` when true→false. Callers should invoke reconcile after
    any signal that can change federation or local gateway catalog snapshots.
    """

    def __init__(self, event_bus: _EventBusPublish | None) -> None:
        self._event_bus = event_bus
        self._watched: set[str] = set()
        self._last_union: set[str] = set()

    def register_watch(self, model_ids: list[str]) -> dict[str, bool]:
        """Merge model IDs into the watch set and return current availability.

        Args:
            model_ids: Identifiers to include; empty strings are skipped.

        Returns:
            Map of each currently watched model_id to whether it appears in the
            last reconciled aggregate union.
        """
        for mid in model_ids:
            if mid:
                self._watched.add(mid)
        return self.snapshot(sorted(self._watched))

    def snapshot(self, model_ids: list[str]) -> dict[str, bool]:
        """Return membership of the given IDs in the last reconciled union."""
        u = self._last_union
        return {m: m in u for m in model_ids}

    async def reconcile(
        self,
        gateway_manager: SingleGatewayManager | None,
        federated_manager: FederatedGatewayManager | None,
    ) -> None:
        """Recompute the aggregate union and emit watched transitions."""
        if self._event_bus is None:
            return

        new_union = set(get_all_available_models(gateway_manager, federated_manager))
        old = self._last_union
        self._last_union = new_union

        from src.scheduling.events.model_lifecycle import (
            ModelAvailable,
            ModelUnavailable,
        )

        for mid in self._watched:
            was = mid in old
            now = mid in new_union
            if was == now:
                continue
            ev = ModelAvailable(mid) if now else ModelUnavailable(mid)
            try:
                await self._event_bus.publish_async_nowait(ev)
            except Exception as exc:
                logger.error(
                    "Failed to emit %s for %s: %s",
                    ev.signal,
                    mid,
                    exc,
                )

    async def reconcile_from_proxy(self, proxy: StargateProxy) -> None:
        """Convenience wrapper reading gateway and federation managers from proxy."""
        gm = getattr(proxy, "gateway_manager", None)
        fm = getattr(proxy, "federated_manager", None)
        await self.reconcile(gm, fm)
