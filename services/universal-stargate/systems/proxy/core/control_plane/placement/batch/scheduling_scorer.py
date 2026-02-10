"""
Generic scheduling scorer for batch routing.

Operates on dict context - NO pipeline type imports.
Scoring factors are injected via generic dict interface.

Domain: Proxy
Algorithm: Topology-Aware First Fit Decreasing (TA-FFD)

Scoring Formula:
    score = w₁ × is_critical_path(model)
          + w₂ × |requests|
          + w₃ × parallel_enablement(model)
          - w₄ × min_depth(model)

Where weights are configurable via stargate_config.yaml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from .config import SchedulingWeights

logger = get_logger(__name__)


class SchedulingScorer:
    """
    Score model groups by scheduling priority.

    Higher scores for models that:
    - Are on the critical path (blocks overall completion)
    - Enable more parallel steps (maximizes throughput)
    - Are used by multiple requests (amortized load cost)
    - Are shallow in DAG (early execution preferred)

    All context passed as dict - no pipeline type knowledge.

    Scoring Complexity: O(n) where n = number of request IDs

    Usage:
        scorer = SchedulingScorer(weights)

        # Score model groups for prioritization
        sorted_groups = sorted(
            model_groups.items(),
            key=lambda item: scorer.score_model_group(
                item[0],
                [r.request_id for r in item[1]],
                scheduling_context,
            ),
            reverse=True,
        )
    """

    def __init__(self, weights: SchedulingWeights) -> None:
        """
        Initialize scorer with configurable weights.

        Args:
            weights: Scoring weights from configuration
        """
        self._weights = weights

    def score_model_group(
        self,
        model_id: ModelId,
        request_ids: list[str],
        scheduling_context: dict | None,
    ) -> float:
        """
        Score a model group for prioritization.

        Args:
            model_id: The model being evaluated
            request_ids: Request IDs (step IDs) for this model
            scheduling_context: Optional dict with pipeline scheduling info:
                - critical_path_step_ids: list[str]
                - step_depths: dict[str, int]
                - parallel_groups: dict[str, list[str]]
                - model_request_counts: dict[str, int]

        Returns:
            Score (higher = prioritize for loading)
        """
        score = 0.0

        # Base score: number of requests (amortized load cost)
        request_count_score = len(request_ids) * self._weights.request_count
        score += request_count_score

        if scheduling_context:
            context_score = self._score_from_context(request_ids, scheduling_context)
            score += context_score
            logger.debug(
                f"Model {model_id} score: base={request_count_score:.1f}, "
                f"context={context_score:.1f}, total={score:.1f}"
            )
        else:
            logger.debug(f"Model {model_id} score: {score:.1f} (no scheduling context)")

        return score

    def _score_from_context(
        self,
        request_ids: list[str],
        ctx: dict,
    ) -> float:
        """
        Apply scheduling context scoring factors.

        Args:
            request_ids: Request/step IDs for this model
            ctx: Scheduling context dict

        Returns:
            Additional score from context factors
        """
        score = 0.0

        critical_path_ids = ctx.get("critical_path_step_ids", [])
        step_depths = ctx.get("step_depths", {})
        parallel_groups = ctx.get("parallel_groups", {})

        # Critical path bonus - any request on critical path
        critical_path_score = self._score_critical_path(request_ids, critical_path_ids)
        score += critical_path_score

        # Parallel enablement bonus - how many siblings does loading this enable?
        enablement_score = self._score_parallel_enablement(request_ids, parallel_groups)
        score += enablement_score

        # Depth penalty - prefer shallow steps (execute early)
        depth_score = self._score_depth_penalty(request_ids, step_depths)
        score += depth_score

        return score

    def _score_critical_path(
        self,
        request_ids: list[str],
        critical_path_ids: list[str],
    ) -> float:
        """
        Score bonus for critical path membership.

        Only counts once per model (not per request).
        """
        critical_path_set = set(critical_path_ids)
        for req_id in request_ids:
            if req_id in critical_path_set:
                return self._weights.critical_path
        return 0.0

    def _score_parallel_enablement(
        self,
        request_ids: list[str],
        parallel_groups: dict[str, list[str]],
    ) -> float:
        """
        Score bonus for enabling parallel siblings.

        Count siblings that would become unblocked if we load this model.
        """
        request_set = set(request_ids)
        enabled_count = 0

        for req_id in request_ids:
            siblings = parallel_groups.get(req_id, [])
            # Count siblings NOT in our request set (they're waiting for us)
            enabled_count += len([s for s in siblings if s not in request_set])

        return enabled_count * self._weights.parallel_enablement

    def _score_depth_penalty(
        self,
        request_ids: list[str],
        step_depths: dict[str, int],
    ) -> float:
        """
        Score penalty for depth (prefer shallow execution).

        Uses minimum depth among all requests for this model.
        """
        if not step_depths:
            return 0.0

        min_depth = float("inf")
        for req_id in request_ids:
            depth = step_depths.get(req_id)
            if depth is not None:
                min_depth = min(min_depth, depth)

        if min_depth == float("inf"):
            return 0.0

        # Penalty increases with depth
        return -min_depth * self._weights.depth_penalty

    def explain_score(
        self,
        model_id: ModelId,
        request_ids: list[str],
        scheduling_context: dict | None,
    ) -> dict:
        """
        Get detailed score breakdown for debugging/observability.

        Returns dict with individual score components.
        """
        breakdown = {
            "model_id": model_id,
            "request_count": len(request_ids),
            "request_count_score": len(request_ids) * self._weights.request_count,
        }

        if scheduling_context:
            critical_path_ids = scheduling_context.get("critical_path_step_ids", [])
            step_depths = scheduling_context.get("step_depths", {})
            parallel_groups = scheduling_context.get("parallel_groups", {})

            breakdown["critical_path_score"] = self._score_critical_path(
                request_ids, critical_path_ids
            )
            breakdown["parallel_enablement_score"] = self._score_parallel_enablement(
                request_ids, parallel_groups
            )
            breakdown["depth_penalty_score"] = self._score_depth_penalty(
                request_ids, step_depths
            )
            breakdown["is_on_critical_path"] = any(
                rid in critical_path_ids for rid in request_ids
            )

        breakdown["total_score"] = sum(
            v
            for k, v in breakdown.items()
            if k.endswith("_score") and isinstance(v, int | float)
        )

        return breakdown
