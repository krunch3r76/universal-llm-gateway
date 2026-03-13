"""Unified routing decision engine with full observability and sticky safeguards.

This module orchestrates feasibility evaluation, scoring, candidate selection,
trace generation, and optional event publication for each routing decision.
Admission control remains delegated to capacity systems outside this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..config import RoutingPolicy
from ..feasibility import evaluate_feasibility
from ..requirements import create_requirements_lookup
from ..scorer import calculate_utility
from ..stability import StickyPlacementTracker
from ..types import DecisionTrace, FeasibilityTier, GatewayCandidate
from .capacity_constraints import is_capacity_constrained
from .selection_rules import apply_selection_rule
from .trace_emission import emit_decision_trace

if TYPE_CHECKING:
    from ...types import Gateway, Placement
    from ..protocols import RoutingKeyTracker

logger = get_logger(__name__)


class DecisionEngine:
    """Select gateways by feasibility tiers, utility scores, and sticky invariants.

    The engine evaluates every candidate, applies affinity and tier rules, and
    returns a selected gateway plus a decision trace for diagnostics. Callers
    may provide a stability tracker and event bus to enrich stateful behavior.
    """

    def __init__(
        self,
        policy: RoutingPolicy,
        event_bus: Any = None,
        emit_traces: bool | None = None,
        routing_key_tracker: RoutingKeyTracker | None = None,
        is_gateway_available_fn: Callable[[str, str], bool] | None = None,
        eviction_cooldown_s: float = 120.0,
        has_demand: Callable[[str], bool] | None = None,
    ) -> None:
        self._policy = policy
        self._event_bus = event_bus
        self._emit_traces = (
            emit_traces if emit_traces is not None else policy.emit_decision_traces
        )
        self._routing_key_tracker = routing_key_tracker
        self._is_gateway_available_fn = is_gateway_available_fn
        self._eviction_cooldown_s = eviction_cooldown_s
        self._has_demand = has_demand

    def select(
        self,
        gateways: list[Gateway],
        placement: Placement,
        request_id: str | None = None,
        sticky: bool | None = None,
        stability_tracker: StickyPlacementTracker | None = None,
    ) -> tuple[Gateway | None, DecisionTrace]:
        """Evaluate and select a best gateway for a placement request.

        The decision process computes feasibility for all gateways, scores
        feasible candidates, applies tier and affinity selection rules, enforces
        sticky-capacity guardrails, and emits a structured trace result.
        """
        start_time = time.perf_counter()

        model_id = placement.model_id
        is_sticky = sticky if sticky is not None else self._policy.is_sticky(model_id)
        affinity_rule = self._policy.find_affinity(model_id)
        hard_affinity = self._policy.find_hard_affinity(model_id)
        current_best = (
            stability_tracker.get_current_best(placement.model_id)
            if stability_tracker
            else None
        )

        candidates: list[GatewayCandidate] = []

        for gateway in gateways:
            logger.debug(
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
                eviction_cooldown_s=self._eviction_cooldown_s,
                has_demand=self._has_demand,
            )

            score_components = None
            if tier != FeasibilityTier.T0_INFEASIBLE:
                score_components, _ = calculate_utility(
                    gateway=gateway,
                    placement=placement,
                    policy=self._policy,
                    tier=tier,
                    eviction_plan=eviction_plan,
                    affinity_rule=affinity_rule,
                    current_best=current_best,
                    sticky=is_sticky,
                )

            candidate = GatewayCandidate(
                gateway=gateway,
                tier=tier,
                constraints_failed=failures,
                score_components=score_components,
                eviction_plan=eviction_plan,
                affinity_rule=(
                    affinity_rule
                    if affinity_rule and affinity_rule.node == gateway.node_id
                    else None
                ),
            )
            candidates.append(candidate)

        selected, reason = self._apply_selection_rule(
            candidates=candidates,
            hard_affinity=hard_affinity,
        )

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
            if bound_candidate and is_capacity_constrained(bound_candidate):
                logger.debug(
                    "🔒 STICKY GUARD: %s bound to %s (at capacity), "
                    "blocking route to %s",
                    placement.model_id,
                    current_best,
                    selected.gateway.name,
                )
                selected = None
                reason = f"sticky_capacity_wait: bound={current_best}, at_capacity"

        if selected and stability_tracker:
            stability_tracker.update_binding(placement.model_id, selected.gateway.name)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        trace = DecisionTrace(
            model_id=str(placement.model_id),
            original_model_id=placement.original_model_id,
            request_id=request_id,
            candidates=tuple(candidates),
            selected_gateway=selected.gateway.name if selected else None,
            selection_reason=reason,
            selection_tier=selected.tier if selected else None,
            evaluation_time_ms=elapsed_ms,
            is_sticky=is_sticky,
        )

        log_dict = trace.to_log_dict()
        if selected:
            is_warm = any(
                c.score_components and c.score_components.warm > 0
                for c in candidates
                if c.gateway.name == selected.gateway.name
            )
            route_type = "warm" if is_warm else "cold"
            logger.info(
                "📊 ROUTING: %s → %s [%s] loaded=%d loading=%d busy=%d",
                placement.model_id,
                selected.gateway.name,
                route_type,
                len(selected.gateway.loaded_models),
                len(selected.gateway.loading_models),
                len(selected.gateway.busy_models),
            )
            logger.debug("Routing decision: %s", log_dict)
        else:
            rejection_details = []
            for candidate in candidates:
                gateway_name = candidate.gateway.name
                tier_str = candidate.tier.name
                if candidate.constraints_failed:
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

            logger.info(
                "❌ No feasible gateway for %s | Reason: %s | "
                "Candidates evaluated: %d | Details: %s",
                placement.model_id,
                reason,
                len(candidates),
                "; ".join(rejection_details),
            )

        if self._emit_traces:
            self._emit_decision_trace(trace)

        return selected.gateway if selected else None, trace

    def select_excluding(
        self,
        gateways: list[Gateway],
        placement: Placement,
        *,
        excluded_gateway_names: frozenset[str],
        request_id: str | None = None,
        sticky: bool | None = None,
        stability_tracker: StickyPlacementTracker | None = None,
    ) -> tuple[Gateway | None, DecisionTrace]:
        """Select a gateway while skipping names already attempted upstream.

        This helper enables retry loops to avoid previous failures while still
        producing a deterministic decision trace for observability and triage.
        """
        filtered = [g for g in gateways if g.name not in excluded_gateway_names]
        if not filtered:
            return (
                None,
                DecisionTrace(
                    model_id=str(placement.model_id),
                    original_model_id=placement.original_model_id,
                    request_id=request_id,
                    selected_gateway=None,
                    selection_reason="no_candidates_after_exclusion",
                    selection_tier=None,
                    is_sticky=(
                        sticky
                        if sticky is not None
                        else self._policy.is_sticky(str(placement.model_id))
                    ),
                ),
            )
        return self.select(
            filtered,
            placement,
            request_id=request_id,
            sticky=sticky,
            stability_tracker=stability_tracker,
        )

    def _apply_selection_rule(
        self,
        candidates: list[GatewayCandidate],
        hard_affinity: Any,
    ) -> tuple[GatewayCandidate | None, str]:
        """Apply ordered affinity and tier rules for final candidate selection.

        The helper preserves prior method-level API while delegating to shared
        pure-selection logic that can be tested independently of engine state.
        """
        return apply_selection_rule(
            candidates=candidates,
            hard_affinity=hard_affinity,
            eviction_margin=self._policy.eviction_margin,
        )

    def _emit_decision_trace(self, trace: DecisionTrace) -> None:
        """Emit a decision trace event through the configured async event bus.

        Emission is best-effort and intentionally non-blocking on request paths.
        Any publication failures are logged for diagnostics without affecting the
        returned routing decision.
        """
        emit_decision_trace(
            event_bus=self._event_bus,
            trace=trace,
            include_candidate_details=self._policy.include_candidate_details,
        )


def create_decision_engine(
    policy: RoutingPolicy,
    event_bus: Any = None,
    routing_key_tracker: RoutingKeyTracker | None = None,
    is_gateway_available_fn: Callable[[str, str], bool] | None = None,
    eviction_cooldown_s: float = 120.0,
    has_demand: Callable[[str], bool] | None = None,
) -> DecisionEngine:
    """Build a decision engine with optional dependency injections for routing.

    The factory centralizes constructor wiring used by routing runtime setup so
    callers avoid repeating optional dependency plumbing across component trees.
    """
    return DecisionEngine(
        policy,
        event_bus=event_bus,
        routing_key_tracker=routing_key_tracker,
        is_gateway_available_fn=is_gateway_available_fn,
        eviction_cooldown_s=eviction_cooldown_s,
        has_demand=has_demand,
    )
