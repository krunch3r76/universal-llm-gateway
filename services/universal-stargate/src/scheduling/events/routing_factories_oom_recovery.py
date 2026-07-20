"""Stargate scheduling routing events — split module covering out-of-memory (OOM) recovery. Builds `Event` objects via `event_factory` for OOM-recovery routing signals defined in this module, keeping OOM-specific factory functions isolated from the other routing-event split modules."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

# ========================================
# OOM Recovery Signals
# ========================================

ROUTING_INFERENCE_OOM_RECOVERY_STARTED = "routing.inference.oom.recovery.started"
"""
OOM recovery initiated: evicting idle models after inference 500.
Payload: request_id, model_id, gateway_id, evicting_count, evicting_models
"""

ROUTING_INFERENCE_OOM_RECOVERY_SUCCEEDED = "routing.inference.oom.recovery.succeeded"
"""
OOM recovery succeeded: retry after eviction returned a non-500 response.
Payload: request_id, model_id, gateway_id, evicted_count
"""


@event_factory
def OomRecoveryStarted(
    request_id: str,
    model_id: str,
    gateway_id: str,
    evicting_count: int,
    evicting_models: list[str],
) -> Event:
    """Emit when OOM recovery begins (evicting idle models)."""
    return Event(
        signal=ROUTING_INFERENCE_OOM_RECOVERY_STARTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "evicting_count": evicting_count,
            "evicting_models": evicting_models,
        },
    )


@event_factory
def OomRecoverySucceeded(
    request_id: str,
    model_id: str,
    gateway_id: str,
    evicted_count: int,
) -> Event:
    """Emit when retry after OOM recovery succeeds."""
    return Event(
        signal=ROUTING_INFERENCE_OOM_RECOVERY_SUCCEEDED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "evicted_count": evicted_count,
        },
    )
