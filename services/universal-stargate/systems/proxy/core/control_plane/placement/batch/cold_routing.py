"""
Cold routing - orchestrates assignment of requests that need model loading.

Coordinates:
- Resource availability checking
- Eviction when needed
- Delegation to TA-FFD assignment algorithm

Phase 6 Enhancements:
- BatchModelTracker integration for cross-batch coordination
- SchedulingScorer for topology-aware prioritization
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import SingleGatewayManager
    from systems.routing.selection.decision.types import Gateway

    from ..single.operations import ModelRoutingOperations
    from .batch import InferenceBatch
    from .model_tracker import BatchModelTracker
    from .request import InferenceRequest
    from .scheduling_scorer import SchedulingScorer

logger = get_logger(__name__)


async def assign_cold_requests(
    cold_requests: list[InferenceRequest],
    gateways: list[Gateway],
    batch: InferenceBatch,
    gateway_manager: SingleGatewayManager,
    routing_ops: ModelRoutingOperations,
    model_tracker: BatchModelTracker | None = None,
    scheduler_scorer: SchedulingScorer | None = None,
) -> None:
    """
    Assign cold requests with resource coordination.

    Algorithm:
    1. Compute total resource requirements
    2. Compute available resources (across all gateways)
    3. If requirements > available:
       a. Compute eviction plan
       b. Execute eviction
       c. If still insufficient, defer some requests
    4. Assign requests to best gateways using TA-FFD

    Args:
        cold_requests: Requests needing model load
        gateways: Current gateway state
        batch: Batch to update with assignments/deferrals
        gateway_manager: For gateway instance lookup
        routing_ops: For eviction execution
        model_tracker: Optional cross-batch model tracker (Phase 6)
        scheduler_scorer: Optional scheduling scorer (Phase 6)
    """
    from .model_assignment import assign_model_groups

    if not cold_requests:
        return

    # Compute and log resource requirements
    total_vram, total_ram = _compute_requirements(cold_requests)
    _validate_batch_aggregates(batch, cold_requests, total_vram, total_ram)

    available_vram = sum(g.vram_free_mb for g in gateways)
    available_ram = sum(g.ram_free_mb for g in gateways)

    logger.info(
        f"Cold routing: need {total_vram}MB VRAM, {total_ram}MB RAM; "
        f"available: {available_vram}MB VRAM, {available_ram}MB RAM"
    )

    # Check if we need eviction
    needs_eviction = (total_vram > available_vram) or (total_ram > available_ram)

    if needs_eviction:
        freed_vram, freed_ram = await _execute_eviction_if_needed(
            cold_requests, gateways, gateway_manager, routing_ops
        )
        available_vram += freed_vram
        available_ram += freed_ram

        if freed_vram > 0 or freed_ram > 0:
            logger.info(
                f"After eviction: {available_vram}MB VRAM, "
                f"{available_ram}MB RAM available"
            )

    # Assign requests using topology-aware first fit decreasing
    await assign_model_groups(
        cold_requests,
        gateways,
        batch,
        routing_ops,
        model_tracker=model_tracker,
        scheduler_scorer=scheduler_scorer,
    )


def _compute_requirements(
    cold_requests: list[InferenceRequest],
) -> tuple[int, int]:
    """Compute total VRAM and RAM requirements."""
    total_vram = sum(r.vram_required_mb for r in cold_requests if r.is_gpu)
    total_ram = sum(r.ram_required_mb for r in cold_requests if not r.is_gpu)
    return total_vram, total_ram


def _validate_batch_aggregates(
    batch: InferenceBatch,
    cold_requests: list[InferenceRequest],
    total_vram: int,
    total_ram: int,
) -> None:
    """Validate batch aggregated totals match recalculated values."""
    if len(cold_requests) != len(batch.requests):
        return  # Only validate when all requests are cold

    if batch.total_vram_mb != total_vram:
        logger.warning(
            f"Resource aggregation mismatch for batch {batch.batch_id}: "
            f"batch.total_vram_mb={batch.total_vram_mb}, recalculated={total_vram}"
        )
    if batch.total_ram_mb != total_ram:
        logger.warning(
            f"Resource aggregation mismatch for batch {batch.batch_id}: "
            f"batch.total_ram_mb={batch.total_ram_mb}, recalculated={total_ram}"
        )


async def _execute_eviction_if_needed(
    cold_requests: list[InferenceRequest],
    gateways: list[Gateway],
    gateway_manager: SingleGatewayManager,
    routing_ops: ModelRoutingOperations,
) -> tuple[int, int]:
    """Execute eviction plan if one can be computed. Returns (freed_vram, freed_ram)."""
    from .eviction_planner import compute_batch_eviction_plan, execute_eviction_plan

    eviction_plan = await compute_batch_eviction_plan(
        cold_requests, gateways, gateway_manager
    )

    if eviction_plan:
        return await execute_eviction_plan(eviction_plan, gateway_manager, routing_ops)

    return 0, 0
