"""
Gateway selection and claim handling for cold routing.

Handles:
- Best gateway selection for cold model loads
- Cross-batch claim coordination via BatchModelTracker
- Race condition recovery (check warm path before retry)

Phase 6 Enhancement: BatchModelTracker integration for cross-batch coordination.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.routing.selection.decision.types import Gateway

    from ..single.operations import ModelRoutingOperations
    from .batch import InferenceBatch
    from .model_tracker import BatchModelTracker
    from .request import InferenceRequest

logger = get_logger(__name__)


async def assign_model_group(
    model_id: ModelId,
    requests: list[InferenceRequest],
    gateways: list[Gateway],
    batch: InferenceBatch,
    routing_ops: ModelRoutingOperations,
    gateway_budgets: dict[str, dict[str, int]],
    model_tracker: BatchModelTracker | None,
) -> str | None:
    """
    Assign all requests for a model to a single gateway.

    Handles cross-batch coordination via model_tracker:
    - If model already loading elsewhere, join that gateway
    - Otherwise, claim load and select best gateway

    Race Condition Fix:
    - When claim fails but no pending gateway found, the model may have
      just finished loading (MODEL_LOADED fired, claim released)
    - MUST check if model is now WARM before retrying claim
    - If warm, use warm path instead of retrying cold claim

    Note: BatchModelTracker methods are synchronous (lock-free per ADR-1).

    Returns:
        Gateway name if assigned, None if deferred
    """
    representative = requests[0]
    required = (
        representative.vram_required_mb
        if representative.is_gpu
        else representative.ram_required_mb
    )
    resource_type = "vram" if representative.is_gpu else "ram"

    # Check if model already being loaded by another batch
    if model_tracker:
        pending_gateway = model_tracker.get_pending_gateway(model_id)
        if pending_gateway:
            logger.debug(
                f"Model {model_id} already loading on {pending_gateway}, joining"
            )
            assign_requests_to_gateway(requests, pending_gateway, batch)
            return pending_gateway

    # Select best gateway for this model
    gateway_name = await select_best_gateway_for_cold(
        representative, gateways, routing_ops, gateway_budgets, resource_type
    )

    if gateway_name:
        # Claim load if tracker available (synchronous - lock-free)
        if model_tracker:
            claimed = model_tracker.claim_model_load(
                model_id, gateway_name, batch.batch_id
            )
            if not claimed:
                result = _handle_claim_race(
                    model_id,
                    requests,
                    gateways,
                    batch,
                    gateway_name,
                    model_tracker,
                    required,
                    resource_type,
                )
                if result.gateway:
                    return result.gateway
                if result.deferred:
                    return None
                # result.continue_assignment means retry succeeded

        # Assign all requests for this model to the same gateway
        assign_requests_to_gateway(requests, gateway_name, batch)

        # Decrement gateway budget only once per model (not per request)
        gateway_budgets[gateway_name][resource_type] -= required
        remaining = gateway_budgets[gateway_name][resource_type]
        logger.debug(
            f"Model {model_id} assigned to {gateway_name} "
            f"({remaining}MB {resource_type} remaining)"
        )
        return gateway_name

    # No gateway can fit - defer ALL requests for this model
    defer_requests(requests, batch, model_id, required, resource_type)
    return None


class _ClaimResult:
    """Result of claim race handling."""

    __slots__ = ("gateway", "deferred", "continue_assignment")

    def __init__(
        self,
        gateway: str | None = None,
        deferred: bool = False,
        continue_ok: bool = False,
    ):
        self.gateway = gateway
        self.deferred = deferred
        self.continue_assignment = continue_ok


def _handle_claim_race(
    model_id: ModelId,
    requests: list[InferenceRequest],
    gateways: list[Gateway],
    batch: InferenceBatch,
    gateway_name: str,
    model_tracker: BatchModelTracker,
    required: int,
    resource_type: str,
) -> _ClaimResult:
    """
    Handle race condition when claim fails.

    Returns:
        _ClaimResult with one of:
        - gateway set: resolved to this gateway
        - deferred=True: requests were deferred
        - continue_assignment=True: retry succeeded, continue with original gateway
    """
    pending_gateway = model_tracker.get_pending_gateway(model_id)
    if pending_gateway:
        logger.debug(f"Lost race for {model_id}, joining {pending_gateway}")
        assign_requests_to_gateway(requests, pending_gateway, batch)
        return _ClaimResult(gateway=pending_gateway)

    # RACE CONDITION FIX:
    # Claim failed but no pending gateway means MODEL_LOADED may have
    # fired and released the claim. Check if model is now WARM.
    warm_gateway = find_warm_gateway(model_id, gateways)
    if warm_gateway:
        logger.debug(
            f"Model {model_id} now loaded on {warm_gateway} "
            f"(race resolved via warm path)"
        )
        assign_requests_to_gateway(requests, warm_gateway, batch)
        return _ClaimResult(gateway=warm_gateway)

    # Model still cold - retry claim once
    claimed = model_tracker.claim_model_load(model_id, gateway_name, batch.batch_id)
    if not claimed:
        logger.warning(f"Failed to claim {model_id} after retry, deferring")
        defer_requests(requests, batch, model_id, required, resource_type)
        return _ClaimResult(deferred=True)

    return _ClaimResult(continue_ok=True)


def find_warm_gateway(
    model_id: ModelId,
    gateways: list[Gateway],
) -> str | None:
    """
    Find gateway where model is already loaded.

    Used during race condition recovery when claim fails but no
    pending gateway exists (model may have just finished loading).

    Args:
        model_id: Model to find
        gateways: Current gateway state

    Returns:
        Gateway name if model is loaded, None otherwise
    """
    routing_key = model_id.routing_key

    for gateway in gateways:
        if model_id in gateway.loaded_models:
            return gateway.name
        # Also check by routing key for variant matching
        for loaded in gateway.loaded_models:
            if loaded.routing_key == routing_key:
                return gateway.name

    return None


async def select_best_gateway_for_cold(
    request: InferenceRequest,
    gateways: list[Gateway],
    routing_ops: ModelRoutingOperations,
    gateway_budgets: dict[str, dict[str, int]],
    resource_type: str,
) -> str | None:
    """
    Select best gateway for a cold model load.

    Filters to gateways with sufficient remaining budget in this batch,
    then uses DecisionEngine for scoring.

    Args:
        request: Representative request for resource requirements
        gateways: Available gateways
        routing_ops: For decision engine access
        gateway_budgets: Remaining budget per gateway
        resource_type: "vram" or "ram"

    Returns:
        Gateway name if found, None otherwise
    """
    from systems.routing.selection.collector import build_placement

    placement = await build_placement(
        request.model_id,
        routing_ops._gateway_manager,
        original_model_id=str(request.model_id),
    )

    if not placement:
        return None

    # Filter to gateways with sufficient budget remaining
    required = request.vram_required_mb if request.is_gpu else request.ram_required_mb
    feasible_gateways = [
        g
        for g in gateways
        if gateway_budgets.get(g.name, {}).get(resource_type, 0) >= required
    ]

    if not feasible_gateways:
        logger.debug(f"No gateway has {required}MB {resource_type} budget remaining")
        return None

    # Use decision engine for selection among feasible
    model_router = routing_ops._gateway_manager.model_router
    selected, _trace = model_router._decision_engine.select(
        feasible_gateways, placement, sticky=request.sticky
    )

    return selected.name if selected else None


def assign_requests_to_gateway(
    requests: list[InferenceRequest],
    gateway_name: str,
    batch: InferenceBatch,
) -> None:
    """Assign all requests to a gateway."""
    for request in requests:
        batch.gateway_assignments[request.request_id] = gateway_name
        logger.debug(
            f"Assigned cold request {request.request_id} "
            f"(model: {request.model_id}) to {gateway_name}"
        )


def defer_requests(
    requests: list[InferenceRequest],
    batch: InferenceBatch,
    model_id: ModelId,
    required: int,
    resource_type: str,
) -> None:
    """Defer all requests for a model."""
    for request in requests:
        batch.deferred_requests.append(request.request_id)
        logger.debug(
            f"Deferred request {request.request_id} (model: {model_id}): "
            f"needs {required}MB {resource_type}, no gateway has capacity"
        )
