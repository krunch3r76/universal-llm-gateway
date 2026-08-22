"""
Early T0/T1 feasibility gates before resource and eviction planning.

Short-circuits on circuit-breaker, health, catalog miss, cloud T1,
already-loaded T1, and in-progress loading states shared by evaluate_feasibility.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from universal_logging import get_logger

from .model_checks import _is_model_available, _is_model_loaded
from .types import ConstraintFailure, EvictionPlanSummary, FeasibilityTier

if TYPE_CHECKING:
    from ..types import Gateway, Placement

logger = get_logger(__name__)

FeasibilityResult = tuple[
    FeasibilityTier,
    tuple[ConstraintFailure, ...],
    EvictionPlanSummary | None,
]


def early_feasibility_gates(
    gateway: Gateway,
    placement: Placement,
    *,
    sticky: bool,
    is_gateway_available_fn: Callable[[str, str], bool] | None,
) -> FeasibilityResult | None:
    """Run checks 0–3.5. Return a terminal result, or None to continue.

    sticky is accepted for call-site parity / logging; gates do not branch on it.
    """
    _ = sticky
    failures: list[ConstraintFailure] = []

    if is_gateway_available_fn:
        if not is_gateway_available_fn(gateway.name, str(placement.model_id)):
            logger.warning(f"❌ Circuit OPEN for {gateway.name} - marking INFEASIBLE")
            failures.append(
                ConstraintFailure(
                    constraint="circuit_breaker",
                    reason=f"Circuit OPEN for {gateway.name}",
                    details={
                        "circuit_open": True,
                        "model_id": str(placement.model_id),
                        "retryable": True,
                    },
                )
            )
            return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    if gateway.health_score < 0.5:
        failures.append(
            ConstraintFailure(
                constraint="is_healthy",
                reason=f"Health score {gateway.health_score:.2f} < 0.5",
                details={"health_score": gateway.health_score},
            )
        )
        return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    if not _is_model_available(gateway, placement):
        logger.info(
            f"❌ Model {placement.model_id} NOT in {gateway.name} catalog. "
            f"Catalog sample: {list(gateway.available_models)[:10]}"
        )
        failures.append(
            ConstraintFailure(
                constraint="has_model_available",
                reason=f"Model {placement.model_id} not in gateway catalog",
                details={"available_models": list(gateway.available_models)[:5]},
            )
        )
        return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    logger.info(f"✅ Model {placement.model_id} found in {gateway.name} catalog")

    # Cloud APIs have no VRAM. Catalog hit is enough for T1 — do not treat
    # advertised models as GPU-resident or enter eviction planning.
    if gateway.is_cloud:
        logger.info(
            f"✅ Model {placement.model_id} on cloud gateway {gateway.name} — T1 "
            "(no VRAM residency)"
        )
        return FeasibilityTier.T1_FEASIBLE_NOW, (), None

    # Admission control handled by CapacityPool (master-local, count-based).
    # busy_models is a telemetry hint — NOT a hard gate.
    if _is_model_loaded(gateway, placement):
        if placement.model_id in gateway.busy_models:
            logger.info(
                f"📊 Model {placement.model_id} loaded (busy per telemetry) on "
                f"{gateway.name} — T1 (CapacityPool handles admission)"
            )
        else:
            logger.info(
                f"✅ Model {placement.model_id} loaded and idle on {gateway.name}"
            )
        return FeasibilityTier.T1_FEASIBLE_NOW, (), None

    # Cold-load in progress: treat as T1 (sticky + CapacityPool placeholder).
    if placement.model_id in gateway.loading_models:
        logger.info(
            f"✅ Model {placement.model_id} loading on {gateway.name} — T1 "
            f"(CapacityPool placeholder guards; "
            f"ensure_model_loaded_on_remote will wait)"
        )
        return FeasibilityTier.T1_FEASIBLE_NOW, (), None

    return None
