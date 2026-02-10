"""
Model assignment using Topology-Aware First Fit Decreasing (TA-FFD).

Groups requests by model and assigns each group to a single gateway,
ensuring coordinated resource allocation for pipeline batches.

Phase 6 Enhancement: SchedulingScorer integration for topology-aware prioritization.
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
    from .scheduling_scorer import SchedulingScorer

logger = get_logger(__name__)


async def assign_model_groups(
    cold_requests: list[InferenceRequest],
    gateways: list[Gateway],
    batch: InferenceBatch,
    routing_ops: ModelRoutingOperations,
    model_tracker: BatchModelTracker | None = None,
    scheduler_scorer: SchedulingScorer | None = None,
) -> None:
    """
    Assign cold requests using Topology-Aware First Fit Decreasing (TA-FFD).

    Algorithm:
    1. Group requests by model_id
    2. Score model groups using scheduler (if available)
    3. Sort by score (highest first)
    4. For each model group:
       a. Check if model already claimed by another batch
       b. If claimed, join existing gateway
       c. Otherwise, select best gateway and claim load
       d. Assign all requests for model to chosen gateway

    Invariant: ∀ model in batch: all requests assigned to same gateway (PIPE-07)

    Args:
        cold_requests: Requests needing model load
        gateways: Current gateway state
        batch: Batch to update with assignments/deferrals
        routing_ops: For gateway selection
        model_tracker: Optional cross-batch tracker (joins existing loads)
        scheduler_scorer: Optional scorer (topology-aware prioritization)
    """
    from .gateway_selection import assign_model_group

    # Group requests by model_id
    requests_by_model = _group_requests_by_model(cold_requests)

    # Extract scheduling context from batch metadata (if available)
    scheduling_context = (
        batch.metadata.get("scheduling_context") if batch.metadata else None
    )

    # Sort model groups by priority
    sorted_groups = sort_model_groups(
        requests_by_model,
        scheduler_scorer,
        scheduling_context,
    )

    # Track remaining capacity PER GATEWAY (not global sum)
    gateway_budgets = {
        g.name: {"vram": g.vram_free_mb, "ram": g.ram_free_mb} for g in gateways
    }

    for model_id, requests in sorted_groups:
        await assign_model_group(
            model_id=model_id,
            requests=requests,
            gateways=gateways,
            batch=batch,
            routing_ops=routing_ops,
            gateway_budgets=gateway_budgets,
            model_tracker=model_tracker,
        )


def _group_requests_by_model(
    requests: list[InferenceRequest],
) -> dict[ModelId, list[InferenceRequest]]:
    """Group requests by model_id."""
    requests_by_model: dict[ModelId, list[InferenceRequest]] = {}
    for request in requests:
        if request.model_id not in requests_by_model:
            requests_by_model[request.model_id] = []
        requests_by_model[request.model_id].append(request)
    return requests_by_model


def sort_model_groups(
    requests_by_model: dict[ModelId, list[InferenceRequest]],
    scheduler_scorer: SchedulingScorer | None,
    scheduling_context: dict | None,
) -> list[tuple[ModelId, list[InferenceRequest]]]:
    """
    Sort model groups by priority for assignment.

    Uses SchedulingScorer if available, otherwise falls back to
    resource-based sorting (largest first).

    Args:
        requests_by_model: Requests grouped by model_id
        scheduler_scorer: Optional scorer for topology-aware ordering
        scheduling_context: Optional context from pipeline

    Returns:
        Sorted list of (model_id, requests) tuples
    """
    if scheduler_scorer:
        return _sort_by_scheduling_score(
            requests_by_model, scheduler_scorer, scheduling_context
        )
    return _sort_by_resource_requirement(requests_by_model)


def _sort_by_scheduling_score(
    requests_by_model: dict[ModelId, list[InferenceRequest]],
    scheduler_scorer: SchedulingScorer,
    scheduling_context: dict | None,
) -> list[tuple[ModelId, list[InferenceRequest]]]:
    """Sort by topology-aware scheduling score (highest first)."""
    sorted_groups = sorted(
        requests_by_model.items(),
        key=lambda item: scheduler_scorer.score_model_group(
            item[0],  # model_id
            [r.request_id for r in item[1]],  # request_ids
            scheduling_context,
        ),
        reverse=True,
    )
    logger.debug(f"Sorted {len(sorted_groups)} model groups by scheduling score")
    return sorted_groups


def _sort_by_resource_requirement(
    requests_by_model: dict[ModelId, list[InferenceRequest]],
) -> list[tuple[ModelId, list[InferenceRequest]]]:
    """Sort by maximum resource requirement (largest first)."""
    sorted_groups = sorted(
        requests_by_model.items(),
        key=lambda item: max(
            r.vram_required_mb if r.is_gpu else r.ram_required_mb for r in item[1]
        ),
        reverse=True,
    )
    logger.debug(
        f"Sorted {len(sorted_groups)} model groups by resource requirement (no scorer)"
    )
    return sorted_groups
