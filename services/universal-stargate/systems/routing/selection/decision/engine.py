"""
Decision engine - unified routing decision with full observability.

Produces (selected_gateway, decision_trace) tuples.

Admission control: CapacityLedger in systems/routing/capacity/
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .config import RoutingPolicy
from .feasibility import evaluate_feasibility
from .requirements import create_requirements_lookup
from .scorer import calculate_utility
from .stability import StickyPlacementTracker
from .types import DecisionTrace, FeasibilityTier, GatewayCandidate

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)


# Capacity constraints that indicate temporary unavailability (retryable).
# Distinguished from permanent failures (is_healthy, has_model_available)
# to allow sticky guard to only block on transient conditions.
# circuit_breaker: temporary (OPEN→HALF_OPEN after recovery_timeout); model IS
# available but gateway is isolated. Treat as transient, not permanent failure.
_CAPACITY_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "has_enough_vram",
        "has_enough_ram",
        "compute_type_capacity",
        "has_gateway_capacity",
        "circuit_breaker",
    }
)


def _is_capacity_constrained(candidate: GatewayCandidate) -> bool:
    """Return True if candidate is T0 due to capacity (not permanent failure)."""
    return any(
        f.constraint in _CAPACITY_CONSTRAINTS for f in candidate.constraints_failed
    )


class DecisionEngine:
    """
    Unified routing decision engine with observability.

    This engine is responsible for:
      - Evaluating feasibility of all gateways for a model placement.
      - Scoring feasibility based on T1/T2 tiers, constraints, etc.
      - Applying selection rules to choose the best gateway.
      - Reserving capacity for selected gateway, releasing non-selected slots.
      - Tracking routing stability and updating the stability tracker.
      - Emitting decision traces for monitoring and debugging.

    Invariant: Every decision produces a DecisionTrace.
    Invariant: Selection respects feasibility tiers (T1 > T2 unless margin exceeded).
    """

    def __init__(
        self,
        policy: RoutingPolicy,
        event_bus: Any = None,
        emit_traces: bool | None = None,
        routing_key_tracker: RoutingKeyTracker | None = None,
        is_gateway_available_fn: Callable[[str], bool] | None = None,
    ):
        self._policy = policy
        self._event_bus = event_bus
        # Use policy config if emit_traces not explicitly provided
        self._emit_traces = (
            emit_traces if emit_traces is not None else policy.emit_decision_traces
        )
        self._routing_key_tracker = routing_key_tracker
        self._is_gateway_available_fn = is_gateway_available_fn

    def select(
        self,
        gateways: list[Gateway],
        placement: Placement,
        request_id: str | None = None,
        sticky: bool | None = None,
        stability_tracker: StickyPlacementTracker | None = None,
    ) -> tuple[Gateway | None, DecisionTrace]:
        """
        Select best gateway for model placement.

        Algorithm:
        1. Evaluate feasibility for ALL gateways
        2. Score utility for feasible gateways (T1 and T2)
        3. Apply selection rule:
           - Hard affinity: restrict to affinity gateway(s)
           - Soft affinity: prefer T1, only choose T2 if margin exceeded

        Admission control: CapacityLedger in systems/routing/capacity/

        Args:
            gateways: Available gateways to consider
            placement: Model placement requirements
            request_id: Request ID for tracing
            sticky: If None, auto-detect from policy. If provided, overrides policy.
            stability_tracker: Optional tracker for routing stability (hysteresis).

        Returns:
            (selected_gateway, decision_trace)
        """
        start_time = time.perf_counter()

        # Determine sticky mode
        model_id = placement.model_id  # Already ModelId, no parse needed
        # Convert to str for policy lookup
        is_sticky = (
            sticky if sticky is not None else self._policy.is_sticky(str(model_id))
        )

        # Find affinity rule
        affinity_rule = self._policy.find_affinity(model_id)  # Pass ModelId
        hard_affinity = self._policy.find_hard_affinity(model_id)  # Pass ModelId

        # Current best for stability
        current_best = (
            stability_tracker.get_current_best(placement.model_id)
            if stability_tracker
            else None
        )

        # Evaluate all candidates
        candidates: list[GatewayCandidate] = []

        # Log gateway state snapshot BEFORE evaluation
        for gateway in gateways:
            logger.info(
                f"GATEWAY STATE at selection time for {placement.model_id}:\n"
                f"  gateway: {gateway.name}\n"
                f"  loaded_models: {list(gateway.loaded_models)}\n"
                f"  loading_models: {list(gateway.loading_models)}\n"
                f"  busy_models: {list(gateway.busy_models)}\n"
                f"  active_requests: {gateway.active_requests}\n"
                f"  vram_free: {gateway.vram_free_mb}MB / {gateway.vram_total_mb}MB\n"
                f"  ram_free: {gateway.ram_free_mb}MB / {gateway.ram_total_mb}MB"
            )

        for gateway in gateways:
            # Build pure in-memory requirements lookup from gateway's cached data
            requirements_lookup = create_requirements_lookup(
                gateway_model_details=gateway.model_details,
            )

            tier, failures, eviction_plan = evaluate_feasibility(
                gateway,
                placement,
                self._policy,
                requirements_lookup,
                sticky=is_sticky,
                routing_key_tracker=self._routing_key_tracker,
                is_gateway_available_fn=self._is_gateway_available_fn,
            )

            # Calculate utility for feasible gateways
            score_components = None
            cached_score = 0.0

            if tier != FeasibilityTier.T0_INFEASIBLE:
                score_components, cached_score = calculate_utility(
                    gateway=gateway,
                    placement=placement,
                    policy=self._policy,
                    tier=tier,
                    eviction_plan=eviction_plan,
                    affinity_rule=affinity_rule,
                    current_best=current_best,
                    sticky=is_sticky,
                )

            # Build eviction summary
            eviction_summary = None
            if eviction_plan:
                eviction_summary = eviction_plan

            candidate = GatewayCandidate(
                gateway=gateway,
                tier=tier,
                constraints_failed=failures,
                score_components=score_components,
                eviction_plan=eviction_summary,
                affinity_rule=(
                    affinity_rule
                    if affinity_rule and affinity_rule.node == gateway.node_id
                    else None
                ),
            )

            # Store cached score for sorting
            object.__setattr__(candidate, "_cached_score", cached_score)

            candidates.append(candidate)

        # Apply selection rule
        selected, reason = self._apply_selection_rule(
            candidates=candidates,
            hard_affinity=hard_affinity,
        )

        # STICKY GUARD: For sticky models with a known binding, prevent routing
        # to an alternative gateway when the bound gateway is temporarily at
        # capacity.  This forces STICKY_CAPACITY → retry instead of violating
        # the "model on at most ONE gateway" invariant.
        # Invariant: sticky ∧ current_best ∧ selected≠current_best
        #            ∧ bound_at_capacity ⟹ selected = None
        if (
            is_sticky
            and current_best
            and selected
            and selected.gateway.name != current_best
        ):
            bound_candidate = next(
                (c for c in candidates if c.gateway.name == current_best),
                None,
            )
            if bound_candidate and _is_capacity_constrained(bound_candidate):
                logger.info(
                    f"🔒 STICKY GUARD: {placement.model_id} bound to "
                    f"{current_best} (at capacity), blocking route to "
                    f"{selected.gateway.name}"
                )
                selected = None
                reason = f"sticky_capacity_wait: bound={current_best}, at_capacity"

        # Update stability tracking
        if selected and stability_tracker:
            stability_tracker.update_binding(placement.model_id, selected.gateway.name)

        # Build trace
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        trace = DecisionTrace(
            model_id=str(placement.model_id),  # Convert to str for serialization
            original_model_id=placement.original_model_id,
            request_id=request_id,
            candidates=tuple(candidates),
            selected_gateway=selected.gateway.name if selected else None,
            selection_reason=reason,
            selection_tier=selected.tier if selected else None,
            evaluation_time_ms=elapsed_ms,
            is_sticky=is_sticky,
        )

        # Log decision
        log_dict = trace.to_log_dict()
        if selected:
            # Determine warm/cold classification
            is_warm = any(
                c.score_components and c.score_components.warm > 0
                for c in candidates
                if c.gateway.name == selected.gateway.name
            )
            route_type = "warm" if is_warm else "cold"
            logger.info(
                f"📊 ROUTING: {placement.model_id} → {selected.gateway.name} "
                f"[{route_type}] loaded={len(selected.gateway.loaded_models)} "
                f"loading={len(selected.gateway.loading_models)} "
                f"busy={len(selected.gateway.busy_models)}"
            )
            logger.debug(f"Routing decision: {log_dict}")
        else:
            # Enhanced logging for rejections - show why each gateway was rejected
            rejection_details = []
            for candidate in candidates:
                gateway_name = candidate.gateway.name
                tier_str = candidate.tier.name
                if candidate.constraints_failed:
                    # Show constraint failures
                    failures = [
                        f"{cf.constraint}:{cf.reason}"
                        for cf in candidate.constraints_failed
                    ]
                    rejection_details.append(
                        f"{gateway_name}[{tier_str}]: {', '.join(failures)}"
                    )
                else:
                    rejection_details.append(
                        f"{gateway_name}[{tier_str}]: no constraints failed"
                    )

            logger.warning(
                f"❌ No feasible gateway for {placement.model_id} | "
                f"Reason: {reason} | "
                f"Candidates evaluated: {len(candidates)} | "
                f"Details: {'; '.join(rejection_details)}"
            )

        # Emit trace for monitoring
        if self._emit_traces:
            self._emit_decision_trace(trace)

        return selected.gateway if selected else None, trace

    def _apply_selection_rule(
        self,
        candidates: list[GatewayCandidate],
        hard_affinity,
    ) -> tuple[GatewayCandidate | None, str]:
        """
        Apply selection rule to choose best candidate.

        Rules:
        1. Hard affinity: restrict to affinity gateway(s) first
        2. Prefer T1 over T2 unless T2 score exceeds T1 by eviction_margin
        3. Within same tier, choose highest score
        4. Deterministic tie-breaker (gateway name)
        """
        # Separate by tier
        t1_candidates = [
            c for c in candidates if c.tier == FeasibilityTier.T1_FEASIBLE_NOW
        ]
        t2_candidates = [
            c for c in candidates if c.tier == FeasibilityTier.T2_FEASIBLE_EVICT
        ]

        # Apply hard affinity filter
        if hard_affinity:
            t1_affinity = [
                c for c in t1_candidates if c.gateway.node_id == hard_affinity.node
            ]
            t2_affinity = [
                c for c in t2_candidates if c.gateway.node_id == hard_affinity.node
            ]

            # If affinity gateway is T1, use it
            if t1_affinity:
                return (
                    t1_affinity[0],
                    f"hard_affinity={hard_affinity.node}, tier=T1",
                )

            # If affinity gateway is T2 and eviction allowed, use it
            if t2_affinity and hard_affinity.evict_if_needed:
                return (
                    t2_affinity[0],
                    f"hard_affinity={hard_affinity.node}, tier=T2_eviction",
                )

            # Hard affinity node is infeasible
            logger.warning(
                f"Hard affinity node {hard_affinity.node} infeasible "
                f"(no T1/T2 candidates). Returning None to trigger wait/error logic."
            )
            return None, f"hard_affinity={hard_affinity.node}_infeasible"

        # Sort by score within each tier (descending), with name tie-breaker
        def sort_key(c: GatewayCandidate) -> tuple[float, str]:
            return (-c.utility_score, c.gateway.name)

        t1_sorted = sorted(t1_candidates, key=sort_key)
        t2_sorted = sorted(t2_candidates, key=sort_key)

        # Best T1 candidate
        best_t1 = t1_sorted[0] if t1_sorted else None
        best_t2 = t2_sorted[0] if t2_sorted else None

        # Selection logic
        if best_t1 and best_t2:
            # Compare with eviction margin
            margin = self._policy.eviction_margin
            if best_t2.utility_score >= best_t1.utility_score + margin:
                return (
                    best_t2,
                    f"T2 preferred (score={best_t2.utility_score:.1f} >= "
                    f"T1 {best_t1.utility_score:.1f} + margin {margin})",
                )
            return (
                best_t1,
                f"T1 preferred (score={best_t1.utility_score:.1f}, "
                f"T2 would need {best_t1.utility_score + margin:.1f})",
            )

        if best_t1:
            return best_t1, f"T1 selected (score={best_t1.utility_score:.1f})"

        if best_t2:
            return (
                best_t2,
                f"T2 selected (score={best_t2.utility_score:.1f}, no T1 available)",
            )

        return None, "no_feasible_gateways"

    def _emit_decision_trace(self, trace: DecisionTrace) -> None:
        """Emit decision trace to event bus (fire-and-forget)."""
        if not self._event_bus:
            return  # No event bus available, skip emission

        try:
            from src.scheduling.events import RoutingDecision, RoutingDecisionFailed

            # Build event payload
            event_data = trace.to_event_payload(
                include_candidates=self._policy.include_candidate_details
            )

            # Choose appropriate factory function based on success/failure
            if trace.selected_gateway:
                event = RoutingDecision(
                    model_id=event_data["model_id"],
                    selection_reason=event_data["selection_reason"],
                    candidate_count=event_data["candidate_count"],
                    feasible_count=event_data["feasible_count"],
                    evaluation_time_ms=event_data["evaluation_time_ms"],
                    timestamp=event_data["timestamp"],
                    original_model_id=event_data.get("original_model_id"),
                    selected_gateway=event_data.get("selected_gateway"),
                    selection_tier=event_data.get("selection_tier"),
                    request_id=event_data.get("request_id"),
                    candidates=event_data.get("candidates"),
                )
            else:
                event = RoutingDecisionFailed(
                    model_id=event_data["model_id"],
                    candidate_count=event_data["candidate_count"],
                    evaluation_time_ms=event_data["evaluation_time_ms"],
                    timestamp=event_data["timestamp"],
                    reason=trace.selection_reason,
                    original_model_id=event_data.get("original_model_id"),
                    request_id=event_data.get("request_id"),
                )

            # Fire and forget - create task to avoid blocking
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(
                    lambda: asyncio.create_task(
                        self._event_bus.publish_async_nowait(event)
                    )
                )
            except RuntimeError:
                # Not in async context, skip emission
                pass

        except Exception as e:
            # Fire and forget - log error but don't fail routing
            logger.debug(f"Failed to emit decision trace: {e}")


def create_decision_engine(
    policy: RoutingPolicy,
    event_bus: Any = None,
    routing_key_tracker: RoutingKeyTracker | None = None,
    is_gateway_available_fn: Callable[[str], bool] | None = None,
) -> DecisionEngine:
    """Factory function for decision engine."""
    return DecisionEngine(
        policy,
        event_bus=event_bus,
        routing_key_tracker=routing_key_tracker,
        is_gateway_available_fn=is_gateway_available_fn,
    )
