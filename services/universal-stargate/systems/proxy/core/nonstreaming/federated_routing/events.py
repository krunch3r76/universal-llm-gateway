"""
Event emission helpers for federated non-streaming routing decisions and failures.

This module centralizes routing event publication so routing branches remain focused
on selection and admission flow while keeping observability behavior consistent.
"""

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from model_id import ModelId
    from universal_event_bus import EventBus

    from systems.federation.common.types import FederatedGateway

logger = get_logger(__name__)


async def _emit_event_safe(event_bus: "EventBus", event: Any, event_name: str) -> None:
    """Publish a routing event and downgrade publish failures to debug-only noise."""
    try:
        await event_bus.publish_nowait(event)
    except Exception as exc:
        logger.debug(f"Failed to emit {event_name} event: {exc}")


async def _emit_routing_resource_gap_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    federated_gateways: list["FederatedGateway"],
) -> None:
    """
    Emit routing.resource.data.missing when catalog entries lack resource payloads.

    When model_id is in a gateway's available_models but not in its model_resources,
    routing fails with missing_gateway_resource_data — not because the model is
    absent, but because resource data wasn't populated yet (startup gap).

    This event distinguishes that case from genuine MODEL_NOT_FOUND.
    """
    from src.scheduling.events import RoutingResourceDataMissing

    gap_gateway_ids = [
        fg.gateway_id
        for fg in federated_gateways
        if model_id in fg.available_models and model_id not in fg.model_resources
    ]
    for gateway_id in gap_gateway_ids:
        await _emit_event_safe(
            event_bus,
            RoutingResourceDataMissing(
                request_id=request_id,
                model_id=str(model_id),
                gateway_ids=[gateway_id],
            ),
            f"routing.resource.data.missing:{gateway_id}",
        )


async def _emit_routing_model_infeasible_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    trace: Any | None,
    excluded_gateway_ids: list[str],
) -> None:
    """Emit a structured infeasible-model event with per-gateway failed constraints."""
    from src.scheduling.events import RoutingModelInfeasible

    gateway_constraints: list[dict[str, Any]] = []
    if trace and trace.candidates:
        gateway_constraints = [
            {
                "gateway": c.gateway.name,
                "constraints": [
                    {"constraint": f.constraint, "reason": f.reason}
                    for f in c.constraints_failed
                ],
            }
            for c in trace.candidates
            if c.constraints_failed
        ]

    await _emit_event_safe(
        event_bus,
        RoutingModelInfeasible(
            request_id=request_id,
            model_id=str(model_id),
            gateway_constraints=gateway_constraints,
            excluded_gateway_ids=excluded_gateway_ids,
        ),
        "routing.model.infeasible",
    )


async def _emit_eviction_classification_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    trace: Any | None,
    classification: str,
    failure_reason: str,
) -> None:
    """
    Emit explicit eviction classification for busy-blocked paths and
    permanently insufficient resource paths.
    """
    from src.scheduling.events import (
        RoutingEvictionBlockedBusy,
        RoutingEvictionInsufficientPermanent,
    )

    gateway_id = "unknown"
    loaded_count = 0
    busy_count = 0
    vram_free = 0
    failed_constraints: list[str] = []

    if trace and trace.candidates:
        target_constraint = (
            "eviction_blocked_by_busy_models"
            if classification == "busy_blocked"
            else "can_fit_with_eviction"
        )
        selected = next(
            (
                c
                for c in trace.candidates
                if any(f.constraint == target_constraint for f in c.constraints_failed)
            ),
            trace.candidates[0],
        )
        gateway_id = selected.gateway.name
        loaded_count = len(selected.gateway.loaded_models)
        busy_count = len(selected.gateway.busy_models)
        vram_free = selected.gateway.vram_free_mb
        failed_constraints = [f.constraint for f in selected.constraints_failed]

    if classification == "busy_blocked":
        candidate_breakdown: list[dict[str, Any]] = []
        if trace and trace.candidates:
            candidate_breakdown = [
                {
                    "gateway_id": c.gateway.name,
                    "loaded_count": len(c.gateway.loaded_models),
                    "busy_count": len(c.gateway.busy_models),
                    "loading_count": len(c.gateway.loading_models),
                    "vram_free": c.gateway.vram_free_mb,
                    "constraints_failed": [f.constraint for f in c.constraints_failed],
                }
                for c in trace.candidates
            ]
        await _emit_event_safe(
            event_bus,
            RoutingEvictionBlockedBusy(
                request_id=request_id,
                model_id=str(model_id),
                gateway_id=gateway_id,
                loaded_count=loaded_count,
                busy_count=busy_count,
                vram_free=vram_free,
                candidate_breakdown=candidate_breakdown,
            ),
            "routing.eviction.blocked.busy",
        )
    elif classification == "permanent_insufficient":
        await _emit_event_safe(
            event_bus,
            RoutingEvictionInsufficientPermanent(
                request_id=request_id,
                model_id=str(model_id),
                gateway_id=gateway_id,
                reason=failure_reason,
                failed_constraints=failed_constraints,
            ),
            "routing.eviction.insufficient.permanent",
        )


async def _emit_overflow_triggered_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    from_gateway: str,
    to_gateway: str,
    reason: str,
) -> None:
    """Emit overflow-triggered signal when non-sticky spillover routing is selected."""
    from src.scheduling.events.routing import RoutingOverflowTriggered

    await _emit_event_safe(
        event_bus,
        RoutingOverflowTriggered(
            request_id=request_id,
            model_id=str(model_id),
            from_gateway=from_gateway,
            to_gateway=to_gateway,
            reason=reason,
        ),
        "routing.overflow.triggered",
    )


async def _emit_overflow_failed_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    tried_gateways: list[str],
    reason: str,
) -> None:
    """
    Emit terminal overflow-failed signal once spillover was attempted but the
    request still died during terminal routing rejection.
    """
    from src.scheduling.events.routing import RoutingOverflowFailed

    await _emit_event_safe(
        event_bus,
        RoutingOverflowFailed(
            request_id=request_id,
            model_id=str(model_id),
            tried_gateways=sorted(tried_gateways),
            reason=reason,
        ),
        "routing.overflow.failed",
    )


async def _emit_overflow_load_started_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    gateway_id: str,
    reason: str,
) -> None:
    """Emit overflow load start before initiating a spillover cold-load operation."""
    from src.scheduling.events.routing import ModelLoadOverflowStarted

    await _emit_event_safe(
        event_bus,
        ModelLoadOverflowStarted(
            request_id=request_id,
            model_id=str(model_id),
            gateway_id=gateway_id,
            reason=reason,
        ),
        "model.load.overflow.started",
    )


async def _emit_overflow_assigned_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    from_gateway: str,
    to_gateway: str,
    depth_before: int,
) -> None:
    """
    Emit overflow assignment event at admission time with prior queue depth context.
    """
    from src.scheduling.events.routing import ModelCapacityOverflowAssigned

    await _emit_event_safe(
        event_bus,
        ModelCapacityOverflowAssigned(
            request_id=request_id,
            model_id=str(model_id),
            from_gateway=from_gateway,
            to_gateway=to_gateway,
            depth_before=depth_before,
        ),
        "model.capacity.overflow.assigned",
    )
