"""Aggregate model availability emitter bootstrap and signal subscriptions.

This module owns the creation of AggregateModelAvailabilityEmitter and the
wiring of catalog / gateway / resource signals that should trigger a full
reconciliation of the aggregate (union) model view presented to clients.

It is intentionally a tiny leaf module with no intra-package dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...proxy import StargateProxy

logger = get_logger(__name__)


def _subscribe_aggregate_model_availability(proxy: StargateProxy) -> None:
    """Subscribe catalog-related signals to reconcile aggregate model availability."""
    emitter = getattr(proxy, "aggregate_availability_emitter", None)
    if proxy.event_bus is None or emitter is None:
        return

    from src.scheduling.events import (
        FEDERATION_GATEWAY_CATALOG_CHANGED,
        GATEWAY_RESOURCE_UPDATE,
        GATEWAY_STATE_CHANGED,
    )

    async def _on_catalog_signal(_event: object) -> None:
        await emitter.reconcile_from_proxy(proxy)

    proxy.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, _on_catalog_signal)
    proxy.event_bus.subscribe_async(
        FEDERATION_GATEWAY_CATALOG_CHANGED, _on_catalog_signal
    )
    proxy.event_bus.subscribe_async(GATEWAY_RESOURCE_UPDATE, _on_catalog_signal)
    logger.info("Subscribed aggregate model availability reconcile handlers")


async def initialize_aggregate_model_availability(proxy: StargateProxy) -> None:
    """Create emitter, subscribe reconciles, and run an initial reconcile.

    The emitter is stored on the proxy as ``aggregate_availability_emitter``.
    After construction it immediately performs a baseline reconciliation and
    then listens for future catalog or resource events so that the aggregate
    view stays consistent without requiring callers to remember to refresh.
    """
    from systems.routing.aggregate_model_availability import (
        AggregateModelAvailabilityEmitter,
    )

    proxy.aggregate_availability_emitter = AggregateModelAvailabilityEmitter(
        proxy.event_bus
    )
    _subscribe_aggregate_model_availability(proxy)
    await proxy.aggregate_availability_emitter.reconcile_from_proxy(proxy)
    logger.info("Aggregate model availability emitter initialized")
