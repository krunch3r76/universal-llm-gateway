"""
Warm routing - assign requests to gateways where model is already loaded.

No resource contention for warm hits since model already consumes resources.
Capacity admission is handled elsewhere; warm routing assigns immediately.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.routing.selection.decision.types import Gateway

    from .batch import InferenceBatch
    from .request import InferenceRequest

logger = get_logger(__name__)


async def assign_warm_requests(
    warm_requests: list[tuple[InferenceRequest, str]],
    gateways: list[Gateway],
    batch: InferenceBatch,
) -> None:
    """
    Assign warm requests to their gateways.

    Warm = model already loaded on gateway.
    Only need to check capacity (active request count).

    Args:
        warm_requests: List of (request, gateway_name) tuples
        gateways: Gateway state for capacity checking
        batch: Batch to update with assignments
    """
    gateway_map = {g.name: g for g in gateways}

    for request, gateway_name in warm_requests:
        gateway = gateway_map.get(gateway_name)
        if not gateway:
            # Gateway disappeared - defer for retry
            logger.warning(
                f"Warm gateway {gateway_name} not found for {request.model_id}"
            )
            batch.deferred_requests.append(request.request_id)
            continue

        # Assign to warm gateway
        batch.gateway_assignments[request.request_id] = gateway_name
        logger.debug(f"Assigned warm request {request.request_id} to {gateway_name}")
