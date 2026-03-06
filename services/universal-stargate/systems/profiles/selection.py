"""Unified model selection — three-tier cascade.

Tier 1: Intelligence profile store (IntelligenceProfileStore.query)
Tier 2: Cloud proxy tag-based selection (CloudProxyClient.select_models)
Tier 3: Empty list (caller handles static fallback)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from intelligence_profiles import IntelligenceProfileStore
from intelligence_profiles.requirements import SelectionRequest

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SelectionResult:
    """Result of unified model selection."""

    models: list[dict[str, Any]]
    selection_path: str


async def select_models(
    request: SelectionRequest,
    *,
    profile_store: IntelligenceProfileStore | None,
    cloud_select_fn: Any | None,
) -> SelectionResult:
    """Run the three-tier model selection cascade.

    Args:
        request: Unified selection parameters.
        profile_store: Intelligence profile store (tier 1). None = skip.
        cloud_select_fn: Async callable for cloud proxy /api/select (tier 2).
            Signature: async (payload: dict) -> dict. None = skip.

    Returns:
        SelectionResult with ranked models and which tier produced them.
    """
    avoid: frozenset[str] = frozenset(request.avoid_models or [])

    # Tier 1: Intelligence profiles
    if profile_store is not None:
        try:
            requirements = request.to_model_requirements()
            model_ids = profile_store.query(requirements)
            model_ids = [m for m in model_ids if m not in avoid]
            if model_ids:
                models = _build_model_entries(model_ids, profile_store, "profile")
                logger.info(
                    "Unified select tier=profiles task=%s count=%d models=%s",
                    request.task,
                    len(model_ids),
                    model_ids,
                )
                return SelectionResult(models=models, selection_path="profiles")
        except Exception:
            logger.exception("Tier 1 (profiles) failed")

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
        except Exception:
            logger.exception("Tier 2 (cloud-proxy) failed")

    # Tier 3: Empty — caller handles static fallback
    logger.warning(
        "Unified select: no models found for task=%s source=%s",
        request.task,
        request.source,
    )
    return SelectionResult(models=[], selection_path="empty")


def _build_model_entries(
    model_ids: list[str],
    store: IntelligenceProfileStore,
    source_label: str,
) -> list[dict[str, Any]]:
    """Build response entries with optional score info from profiles."""
    entries: list[dict[str, Any]] = []
    for mid in model_ids:
        entry: dict[str, Any] = {"id": mid, "source": source_label}
        profile = store.get(mid)
        if profile is not None:
            entry["cost_bucket"] = profile.cost_bucket
            entry["latency_bucket"] = profile.latency_bucket
        entries.append(entry)
    return entries
