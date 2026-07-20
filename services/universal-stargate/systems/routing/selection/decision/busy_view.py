"""Tracker-over-telemetry busy/idle classification for eviction planning."""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from ..types import Gateway
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)


def actually_busy_models(
    gateway: Gateway,
    routing_key_tracker: RoutingKeyTracker | None = None,
    gw_keys_in_flight: set[str] | None = None,
) -> set[ModelId]:
    """Return loaded models with verified in-flight requests."""
    keys = gw_keys_in_flight
    if keys is None and routing_key_tracker is not None:
        keys = routing_key_tracker.get_routing_keys_in_flight(gateway.name)
    return {
        mid
        for mid in gateway.loaded_models
        if _is_model_actually_busy(gateway, mid, routing_key_tracker, keys)
    }


def idle_models(
    gateway: Gateway,
    routing_key_tracker: RoutingKeyTracker | None = None,
    gw_keys_in_flight: set[str] | None = None,
) -> list[ModelId]:
    """Return loaded models that are idle and not currently loading."""
    busy = actually_busy_models(gateway, routing_key_tracker, gw_keys_in_flight)
    return [
        mid
        for mid in gateway.loaded_models
        if mid not in busy and mid not in gateway.loading_models
    ]


def _is_model_actually_busy(
    gateway: Gateway,
    model_id: ModelId,
    routing_key_tracker: RoutingKeyTracker | None,
    gw_keys_in_flight: set[str] | None = None,
) -> bool:
    """Return True iff the model has verified in-flight requests.

    INVARIANT: tracker_in_flight(model_id, gateway) implies busy(model_id).

    The routing_key_tracker is checked first because it is the master's
    authoritative record of requests it dispatched and not yet completed.
    Telemetry (busy_models) is a best-effort hint from the edge; it can be
    momentarily stale and MUST NOT override a positive tracker signal.

    Decision matrix:
      tracker has keys   -> busy (regardless of telemetry)
      no tracker         -> telemetry alone decides
      tracker, no keys   -> idle (telemetry "busy" treated as stale)
    """
    if routing_key_tracker is not None:
        keys_in_flight = (
            gw_keys_in_flight
            if gw_keys_in_flight is not None
            else routing_key_tracker.get_routing_keys_in_flight(gateway.name)
        )
        if model_id.routing_key in keys_in_flight:
            return True
        if model_id in gateway.busy_models:
            logger.info(
                f"📊 Stale busy_models detected: {model_id} on {gateway.name} "
                f"is busy per telemetry but idle per routing tracker"
            )
        return False

    return model_id in gateway.busy_models
