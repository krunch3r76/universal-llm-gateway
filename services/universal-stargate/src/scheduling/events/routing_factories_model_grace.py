"""Stargate scheduling routing events — split module covering model grace-period routing: grace-queued, grace-resolved, and grace-timeout signals, built as `Event` objects marking a request's wait for a model to finish its post-load grace window."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

from .routing_signal_constants_routing_waits import (
    ROUTING_MODEL_GRACE_QUEUED,
    ROUTING_MODEL_GRACE_RESOLVED,
    ROUTING_MODEL_GRACE_TIMEOUT,
)


@event_factory
def RoutingModelGraceQueued(
    request_id: str,
    model_id: str,
    timeout_s: float,
    unhealthy_gateway_ids: list[str],
) -> Event:
    """
    Emit when a request enters model-scoped grace waiting.

    This fires when healthy gateways are present but none currently advertise
    the requested model while at least one unhealthy gateway still does.
    """
    return Event(
        signal=ROUTING_MODEL_GRACE_QUEUED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "timeout_s": timeout_s,
            "unhealthy_gateway_ids": unhealthy_gateway_ids,
        },
    )


@event_factory
def RoutingModelGraceResolved(
    request_id: str,
    model_id: str,
    gateway_id: str,
    waited_ms: int,
) -> Event:
    """
    Emit when model-scoped grace unblocks after model gateway recovery.

    Carries the recovering gateway ID and end-to-end grace wait duration.
    """
    return Event(
        signal=ROUTING_MODEL_GRACE_RESOLVED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "waited_ms": waited_ms,
        },
    )


@event_factory
def RoutingModelGraceTimeout(
    request_id: str,
    model_id: str,
    waited_ms: int,
) -> Event:
    """
    Emit when model-scoped grace expires without model gateway recovery.

    Downstream routing continues through existing infeasible/error paths.
    """
    return Event(
        signal=ROUTING_MODEL_GRACE_TIMEOUT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "waited_ms": waited_ms,
        },
    )
