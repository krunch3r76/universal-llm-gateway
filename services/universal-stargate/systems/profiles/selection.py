"""Unified model selection — three-tier cascade.

Tier 1: Intelligence profile store (IntelligenceProfileStore.query)
Tier 2: Cloud proxy tag-based selection (CloudProxyClient.select_models)
Tier 3: Static defaults (server-owned fallback)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any

from intelligence_profiles import IntelligenceProfileStore
from intelligence_profiles.requirements import SelectionRequest

from src.scheduling.events.request import (
    ModelSelectionRankComputed,
    ModelSelectionScoreUpdated,
    ModelSelectionSwitchAllowed,
    ModelSelectionSwitchSuppressed,
)

from .exclusions import get_excluded_models, load_exclusions
from .reputation_policy import DEFAULT_REPUTATION_POLICY, ReputationPolicy
from .reputation_scorer import ReputationScore, score_record
from .reputation_store import TaskModelReputationStore

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = logging.getLogger(__name__)

type CloudSelectFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_STATIC_DEFAULT_MODEL_IDS: tuple[str, ...] = (
    "google/gemini-2.5-flash",
    "deepseek/deepseek-r1-0528",
    "anthropic/claude-sonnet-4",
)


@dataclass(slots=True)
class SelectionResult:
    """Result of unified model selection."""

    models: list[dict[str, Any]]
    selection_path: str


async def select_models(
    request: SelectionRequest,
    *,
    profile_store: IntelligenceProfileStore | None,
    cloud_select_fn: CloudSelectFn | None,
    reputation_store: TaskModelReputationStore | None = None,
    policy: ReputationPolicy = DEFAULT_REPUTATION_POLICY,
    event_bus: EventBus | None = None,
) -> SelectionResult:
    """Run the three-tier model selection cascade.

    Args:
        request: Unified selection parameters.
        profile_store: Intelligence profile store (tier 1). None = skip.
        cloud_select_fn: Async callable for cloud proxy /api/select (tier 2).
            Signature: async (payload: dict) -> dict. None = skip.
        event_bus: Optional event bus for reputation signal emission.

    Returns:
        SelectionResult with ranked models and which tier produced them.
    """
    avoid: frozenset[str] = frozenset(request.avoid_models or [])

    exclusions = load_exclusions()
    excluded = get_excluded_models(request.task, exclusions)
    if excluded:
        avoid = avoid | excluded
        logger.info(
            "Exclusions applied for task=%s: %s",
            request.task,
            sorted(excluded),
        )

    # Tier 1: Intelligence profiles
    if profile_store is not None:
        try:
            requirements = request.to_model_requirements()
            model_ids = profile_store.query(requirements)
            model_ids = [m for m in model_ids if m not in avoid]
            if request.source in {"cloud", "local"}:
                source_is_cloud = request.source == "cloud"
                filtered = [m for m in model_ids if ("/" in m) is source_is_cloud]
                dropped = len(model_ids) - len(filtered)
                if dropped:
                    logger.info(
                        "Tier=profiles source=%s dropped=%d remain=%d",
                        request.source,
                        dropped,
                        len(filtered),
                    )
                model_ids = filtered
            if model_ids:
                selection_path = "profiles"
                scores_by_id: dict[str, ReputationScore] = {}
                if request.health_scoring and reputation_store is not None:
                    model_ids, scores_by_id = _apply_reputation_ranking(
                        model_ids=model_ids,
                        task=request.task,
                        reputation_store=reputation_store,
                        policy=policy,
                        sticky_key=request.selection_sticky_key,
                        event_bus=event_bus,
                    )
                    selection_path = "profiles+reputation"
                    _emit_reputation_events(
                        task=request.task,
                        model_ids=model_ids,
                        scores_by_id=scores_by_id,
                        selection_path=selection_path,
                        event_bus=event_bus,
                    )

                models = _build_model_entries(
                    model_ids,
                    profile_store,
                    "profile",
                    include_health_scores=request.include_health_scores,
                    scores=scores_by_id,
                )
                logger.info(
                    "Unified select tier=profiles task=%s count=%d models=%s",
                    request.task,
                    len(model_ids),
                    model_ids,
                )
                return SelectionResult(models=models, selection_path=selection_path)
        except Exception as error:
            # Keep unified select resilient: tier failures must degrade gracefully.
            logger.exception("Tier 1 (profiles) failed: %s", error)

    # Tier 2: Cloud proxy tag-based selection
    if cloud_select_fn is not None and request.tags:
        try:
            cloud_payload: dict[str, Any] = {"count": request.count + len(avoid)}
            if request.tags:
                cloud_payload["tags"] = request.tags
            if request.exclude_tags:
                cloud_payload["exclude_tags"] = request.exclude_tags
            if request.min_context is not None:
                cloud_payload["min_context"] = request.min_context
            if request.min_tier is not None:
                cloud_payload["min_tier"] = request.min_tier

            result = await cloud_select_fn(cloud_payload)
            raw_models = result.get("models", []) if isinstance(result, dict) else []
            if raw_models:
                models = [
                    {"id": m["id"], "source": "cloud"}
                    for m in raw_models
                    if isinstance(m, dict) and m.get("id") and m["id"] not in avoid
                ][: request.count]
                if models:
                    logger.info(
                        "Unified select tier=cloud-proxy task=%s count=%d models=%s",
                        request.task,
                        len(models),
                        [m["id"] for m in models],
                    )
                    return SelectionResult(models=models, selection_path="cloud-proxy")
        except Exception as error:
            # Keep unified select resilient: tier failures must degrade gracefully.
            logger.exception("Tier 2 (cloud-proxy) failed: %s", error)

    # Tier 3: Static defaults (server-owned fallback)
    fallback_ids = _fallback_model_ids_for_source(
        request.source,
        avoid=avoid,
    )[: request.count]
    if fallback_ids:
        logger.warning(
            "Unified select tier=static-defaults task=%s source=%s count=%d models=%s",
            request.task,
            request.source,
            len(fallback_ids),
            fallback_ids,
        )
        return SelectionResult(
            models=_build_static_entries(fallback_ids),
            selection_path="static-defaults",
        )

    logger.warning(
        (
            "Unified select: no models found (including static defaults) "
            "for task=%s source=%s"
        ),
        request.task,
        request.source,
    )
    return SelectionResult(models=[], selection_path="empty")


def _apply_reputation_ranking(
    model_ids: list[str],
    *,
    task: str,
    reputation_store: TaskModelReputationStore,
    policy: ReputationPolicy,
    sticky_key: str | None,
    event_bus: EventBus | None = None,
) -> tuple[list[str], dict[str, ReputationScore]]:
    """Rank model_ids by reputation score, apply anti-thrash stickiness.

    Returns:
        (ranked_model_ids, scores_by_id)
    """
    scored = [
        score_record(
            model_id=mid,
            record=reputation_store.get(task, mid),
            policy=policy,
        )
        for mid in model_ids
    ]
    scored.sort(key=lambda s: s.final_score, reverse=True)

    if sticky_key and scored:
        scored = _apply_anti_thrash(
            scored=scored,
            sticky_key=sticky_key,
            task=task,
            reputation_store=reputation_store,
            policy=policy,
            event_bus=event_bus,
        )

    if sticky_key and scored:
        reputation_store.remember_selection(sticky_key, scored[0].model_id)

    return (
        [s.model_id for s in scored],
        {s.model_id: s for s in scored},
    )


def _apply_anti_thrash(
    scored: list[ReputationScore],
    sticky_key: str,
    task: str,
    reputation_store: TaskModelReputationStore,
    policy: ReputationPolicy,
    *,
    event_bus: EventBus | None = None,
) -> list[ReputationScore]:
    """Suppress marginal switches within cooldown.

    INVARIANT: ∀ (contender, previous) within cooldown:
      delta < min_switch_delta ⟹ previous promoted to rank 0
    """
    previous = reputation_store.get_last_selection(sticky_key)
    if previous is None:
        return scored
    previous_id, previous_ts = previous
    contender = scored[0]
    if contender.model_id == previous_id:
        return scored
    if (monotonic() - previous_ts) >= policy.switch_cooldown_s:
        return scored
    previous_score = next(
        (s for s in scored if s.model_id == previous_id),
        None,
    )
    if previous_score is None:
        return scored
    delta = contender.final_score - previous_score.final_score
    if delta < policy.min_switch_delta:
        if event_bus is not None:
            asyncio.create_task(
                event_bus.publish_nowait(
                    ModelSelectionSwitchSuppressed(
                        task=task,
                        sticky_key=sticky_key,
                        current_model_id=previous_score.model_id,
                        contender_model_id=contender.model_id,
                        delta=delta,
                        reason="below_switch_delta_within_cooldown",
                    )
                )
            )
        new_scored = [s for s in scored if s.model_id != previous_id]
        new_scored.insert(0, previous_score)
        scored = new_scored
    else:
        if event_bus is not None:
            asyncio.create_task(
                event_bus.publish_nowait(
                    ModelSelectionSwitchAllowed(
                        task=task,
                        sticky_key=sticky_key,
                        previous_model_id=previous_score.model_id,
                        new_model_id=contender.model_id,
                        delta=delta,
                    )
                )
            )
    return scored


def _emit_reputation_events(
    *,
    task: str,
    model_ids: list[str],
    scores_by_id: dict[str, ReputationScore],
    selection_path: str,
    event_bus: EventBus | None = None,
) -> None:
    """Publish per-model reputation details and final ranking events."""
    if event_bus is None:
        return
    for model_id in model_ids:
        score = scores_by_id[model_id]
        asyncio.create_task(
            event_bus.publish_nowait(
                ModelSelectionScoreUpdated(
                    task=task,
                    model_id=model_id,
                    final_score=score.final_score,
                    components={
                        "reliability": score.components.reliability,
                        "latency": score.components.latency,
                        "quality": score.components.quality,
                        "confidence": score.components.confidence,
                        "prior": score.components.prior,
                        "observed": score.components.observed,
                    },
                )
            )
        )
    asyncio.create_task(
        event_bus.publish_nowait(
            ModelSelectionRankComputed(
                task=task,
                candidates=[
                    {
                        "model_id": model_id,
                        "final_score": scores_by_id[model_id].final_score,
                    }
                    for model_id in model_ids
                ],
                selection_path=selection_path,
            )
        )
    )


def _build_model_entries(
    model_ids: list[str],
    store: IntelligenceProfileStore,
    source_label: str,
    *,
    include_health_scores: bool | None = None,
    scores: dict[str, ReputationScore] | None = None,
) -> list[dict[str, Any]]:
    """Build response entries with optional score info from profiles."""
    entries: list[dict[str, Any]] = []
    for mid in model_ids:
        entry: dict[str, Any] = {"id": mid, "source": source_label}
        profile = store.get(mid)
        if profile is not None:
            entry["cost_bucket"] = profile.cost_bucket
            entry["latency_bucket"] = profile.latency_bucket
        if include_health_scores and scores and mid in scores:
            score = scores[mid]
            entry["health_score"] = score.final_score
            entry["health_components"] = {
                "reliability": score.components.reliability,
                "latency": score.components.latency,
                "quality": score.components.quality,
                "confidence": score.components.confidence,
                "prior": score.components.prior,
                "observed": score.components.observed,
            }
        entries.append(entry)
    return entries


def _fallback_model_ids_for_source(
    source: str,
    *,
    avoid: frozenset[str],
) -> list[str]:
    """Filter static fallback models by requested source and avoid list."""
    if source == "cloud":
        candidates = [m for m in _STATIC_DEFAULT_MODEL_IDS if "/" in m]
    elif source == "local":
        candidates = [m for m in _STATIC_DEFAULT_MODEL_IDS if "/" not in m]
    else:
        candidates = list(_STATIC_DEFAULT_MODEL_IDS)
    return [m for m in candidates if m not in avoid]


def _build_static_entries(model_ids: list[str]) -> list[dict[str, Any]]:
    """Build static fallback response entries."""
    return [{"id": model_id, "source": "static-default"} for model_id in model_ids]
