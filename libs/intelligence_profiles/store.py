"""Intelligence profile store — curated YAML + derived profiles, unified lookup.

Layer priority (field-level merge, highest wins):
  1. Curated YAML  (config/intelligence_profiles/*.yaml)
  2. Auto-derived  (from ProfileDeriver at runtime)
  3. Defaults      (empty IntelligenceProfile for unknown models)
"""

from __future__ import annotations

import logging
import random
from itertools import groupby
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .requirements import LATENCY_ORDER, ModelRequirements
from .schema import SCORE_ORDER, IntelligenceProfile, score_gte

logger = logging.getLogger(__name__)


class IntelligenceProfileStore:
    """Stores and queries intelligence profiles from multiple layers."""

    def __init__(self) -> None:
        self._curated: dict[str, IntelligenceProfile] = {}
        self._derived: dict[str, IntelligenceProfile] = {}

    @property
    def count(self) -> int:
        return len(set(self._curated) | set(self._derived))

    def load_curated(self, directory: Path) -> int:
        """Load curated YAML profiles from a directory. Returns count loaded."""
        if not directory.is_dir():
            logger.warning("Curated profile directory does not exist: %s", directory)
            return 0

        loaded = 0
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text())
                if not isinstance(data, dict):
                    continue
                profile = IntelligenceProfile.model_validate(data)
                key = profile.full_model_id or profile.basename
                self._curated[key] = profile
                loaded += 1
            except (yaml.YAMLError, ValidationError, OSError) as e:
                logger.exception("Failed to load curated profile %s: %s", path, e)

        logger.info("Loaded %d curated profiles from %s", loaded, directory)
        return loaded

    def set_derived(self, model_id: str, profile: IntelligenceProfile) -> None:
        """Insert or replace a derived profile."""
        self._derived[model_id] = profile

    def set_derived_bulk(self, profiles: dict[str, IntelligenceProfile]) -> None:
        """Replace all derived profiles at once (e.g. after catalog refresh)."""
        self._derived = dict(profiles)
        logger.info("Bulk-updated %d derived profiles", len(profiles))

    def get(self, model_id: str) -> IntelligenceProfile | None:
        """Look up a profile by model ID, merging curated over derived."""
        curated = self._curated.get(model_id)
        derived = self._derived.get(model_id)
        if curated and derived:
            return _merge_profiles(curated, derived)
        return curated or derived

    def query(self, requirements: ModelRequirements) -> list[str]:
        """Return ranked model IDs matching the given requirements.

        Filters by task score, context, tool support, source, and cost.
        Ranks by task score descending. Within each score tier, order is
        randomised so repeated calls with the same requirements rotate
        through the available pool rather than always returning the same
        subset.
        Applies provider diversity constraint if specified.
        Returns at most requirements.count IDs.
        """
        all_ids = set(self._curated) | set(self._derived)
        scored: list[tuple[int, str]] = []

        req_for_filter = requirements
        if req_for_filter.min_completion_tokens is None:
            req_for_filter = req_for_filter.model_copy(
                update={"min_completion_tokens": 16384}
            )

        for model_id in all_ids:
            profile = self.get(model_id)
            if profile is None:
                continue

            if not _matches_requirements(profile, model_id, req_for_filter):
                continue

            task_score = _task_score_value(profile, requirements.task)
            scored.append((task_score, model_id))

        scored.sort(key=lambda t: -t[0])

        model_ids: list[str] = []
        for _score, group in groupby(scored, key=lambda t: t[0]):
            tier = [mid for _, mid in group]
            random.shuffle(tier)
            model_ids.extend(tier)
        if (
            requirements.provider_diversity
            and requirements.provider_diversity.min_unique > 1
        ):
            model_ids = _apply_provider_diversity(
                model_ids, requirements.provider_diversity.min_unique
            )

        return model_ids[: requirements.count]


def _matches_requirements(
    profile: IntelligenceProfile,
    model_id: str,
    req: ModelRequirements,
) -> bool:
    """Check whether a profile satisfies the hard constraints."""
    if req.min_score:
        task_entry = profile.tasks.get(req.task)
        task_score = getattr(task_entry, "score", None) if task_entry else None
        if not score_gte(task_score, req.min_score):
            return False

    if req.require_tools and not score_gte(profile.tool_usage_skill, "neutral"):
        return False

    if req.cost_budget and req.cost_budget.max_per_model is not None:
        cost = _extract_cost(profile)
        if cost is not None and cost > req.cost_budget.max_per_model:
            return False

    if req.source != "any":
        source = _extract_source(profile, model_id)
        if source and source != req.source:
            return False

    if req.max_latency_bucket is not None and profile.latency_bucket is not None:
        if (
            LATENCY_ORDER[profile.latency_bucket]
            > LATENCY_ORDER[req.max_latency_bucket]
        ):
            return False

    if req.min_context is not None:
        context = getattr(profile, "context_length", 0) or 0
        if context < req.min_context:
            logger.debug(
                "model.selection.filtered",
                extra={"model_id": model_id, "reason": "min_context"},
            )
            return False

    effective_min_completion_tokens = (
        req.min_completion_tokens if req.min_completion_tokens is not None else 16384
    )
    if req.min_completion_tokens is not None:
        max_completion = getattr(profile, "max_completion_tokens", None)
        if (
            max_completion is not None
            and max_completion < effective_min_completion_tokens
        ):
            logger.debug(
                "model.selection.filtered",
                extra={
                    "model_id": model_id,
                    "reason": "min_completion_tokens",
                },
            )
            return False

    return True


def _task_score_value(profile: IntelligenceProfile, task: str) -> int:
    """Numeric score for sorting — higher is better."""
    entry = profile.tasks.get(task)
    score = getattr(entry, "score", None) if entry else None
    return SCORE_ORDER.get(score, -1) if score else -1


def _extract_cost(profile: IntelligenceProfile) -> float | None:
    """Extract completion cost from extra fields if available.

    Negative costs (OpenRouter sentinel for variable/aggregate pricing) are
    treated as unknown so models sort to the bottom of the cost tiebreaker.
    """
    cost = getattr(profile, "completion_cost", None)
    if isinstance(cost, int | float) and cost >= 0:
        return float(cost)
    return None


def _extract_source(profile: IntelligenceProfile, model_id: str) -> str | None:
    """Determine model source from profile or model_id pattern."""
    source = getattr(profile, "source", None)
    if isinstance(source, str):
        return source
    if "/" in model_id:
        return "cloud"
    return None


def _apply_provider_diversity(model_ids: list[str], min_unique: int) -> list[str]:
    """Reorder to ensure at least min_unique providers appear early."""
    seen_providers: set[str] = set()
    diverse: list[str] = []
    remaining: list[str] = []

    for mid in model_ids:
        provider = mid.split("/", 1)[0] if "/" in mid else ""
        if provider and provider not in seen_providers:
            seen_providers.add(provider)
            diverse.append(mid)
        else:
            remaining.append(mid)

    result = diverse + remaining
    if len(seen_providers) < min_unique:
        logger.warning(
            "Provider diversity: wanted %d unique, got %d",
            min_unique,
            len(seen_providers),
        )
    return result


def _merge_profiles(
    curated: IntelligenceProfile, derived: IntelligenceProfile
) -> IntelligenceProfile:
    """Field-level merge: curated values override derived, missing fields fall through."""
    derived_data: dict[str, Any] = derived.model_dump()
    curated_data: dict[str, Any] = curated.model_dump(exclude_unset=True)
    merged = {**derived_data, **curated_data}
    return IntelligenceProfile.model_validate(merged)
