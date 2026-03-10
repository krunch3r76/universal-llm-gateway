"""Retrieval profiles: loading, caching, and multi-layer parameter resolution.

Resolution precedence (highest to lowest):
    runtime pipeline_options > exact profile[consumer_model] > model_class[consumer_model]
    > tier[consumer_tier] > yaml_defaults

The ``resolve_retrieval_params`` function implements this merge and is called by
the ``rag_multi_retrieve_v1`` handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

logger = get_logger(__name__)

PROFILES_PATH = Path(__file__).resolve().parent.parent / "retrieval-profiles.yaml"
"""Absolute path to ``retrieval-profiles.yaml``, resolved relative to the rag_context_v1 package root."""

_profiles_cache: dict[str, Any] | None = None
"""In-process cache for the loaded retrieval profiles dict; ``None`` until first load."""


def load_retrieval_profiles() -> dict[str, Any]:
    """Load retrieval profiles from YAML (cached after first load).

    Returns top-level dict with ``profiles``, ``tiers``, and ``scope_defaults`` keys.
    Returns empty dict if file is missing or malformed.
    """
    global _profiles_cache  # noqa: PLW0603
    if _profiles_cache is not None:
        return _profiles_cache

    if not PROFILES_PATH.exists():
        logger.info("No retrieval profiles at %s", PROFILES_PATH)
        result: dict[str, Any] = {}
        _profiles_cache = result
        return result

    with PROFILES_PATH.open() as f:
        loaded = yaml.safe_load(f)
    if loaded is None:
        result: dict[str, Any] = {}
    elif not isinstance(loaded, dict):
        logger.warning(
            "Retrieval profiles at %s parsed as %s, expected dict; ignoring",
            PROFILES_PATH,
            type(loaded).__name__,
        )
        result = {}
    else:
        result = loaded

    _profiles_cache = result
    logger.info(
        "Loaded retrieval profiles: %d model(s), %d tier(s), %d scope default(s)",
        len(result.get("profiles", {})),
        len(result.get("tiers", {})),
        len(result.get("scope_defaults", {})),
    )
    return result


@dataclass(slots=True, kw_only=True)
class ResolvedParams:
    """Result of multi-layer retrieval parameter resolution."""

    consumer_tier: str | None
    consumer_model: str
    matched_class_name: str | None
    tier_profile: dict[str, Any]
    model_class_profile: dict[str, Any]
    exact_model_profile: dict[str, Any]
    effective: dict[str, Any]
    top_k: int
    max_chunks: int
    rrf_k: int
    recency_weight: float
    confidence_threshold: float


def resolve_retrieval_params(
    *,
    yaml_defaults: dict[str, Any],
    runtime: dict[str, Any],
    profiles_data: dict[str, Any],
    step_id: str,
) -> ResolvedParams:
    """Resolve retrieval parameters via multi-layer merge.

    Precedence (highest to lowest):
        runtime > exact_model > model_class > tier > yaml_defaults
    """
    consumer_tier: str | None = runtime.get("consumer_tier")
    tier_profile: dict[str, Any] = (
        profiles_data.get("tiers", {}).get(consumer_tier, {}) if consumer_tier else {}
    )
    if consumer_tier and tier_profile:
        logger.info("Step '%s': applying tier profile '%s'", step_id, consumer_tier)
    elif consumer_tier and not tier_profile:
        logger.warning(
            "Step '%s': unknown consumer_tier '%s', ignoring", step_id, consumer_tier
        )

    consumer_model: str = runtime.get("consumer_model", "")
    exact_model_profile: dict[str, Any] = profiles_data.get("profiles", {}).get(
        consumer_model, {}
    )

    matched_class_name: str | None = None
    model_class_profile: dict[str, Any] = {}
    if consumer_model and not exact_model_profile:
        for class_name, class_config in profiles_data.get("model_classes", {}).items():
            pattern = class_config.get("match", "")
            if not pattern:
                continue
            segments = pattern.split("|") if "|" in pattern else [pattern]
            if any(seg in consumer_model for seg in segments):
                model_class_profile = {
                    k: v for k, v in class_config.items() if k != "match"
                }
                matched_class_name = class_name
                logger.info(
                    "Step '%s': no exact profile for '%s', "
                    "using model_class '%s' (match='%s')",
                    step_id,
                    consumer_model,
                    class_name,
                    pattern,
                )
                break

    if consumer_model and exact_model_profile:
        logger.info(
            "Step '%s': applying exact retrieval profile for consumer '%s'",
            step_id,
            consumer_model,
        )

    effective: dict[str, Any] = {
        **yaml_defaults,
        **tier_profile,
        **model_class_profile,
        **exact_model_profile,
        **runtime,
    }

    return ResolvedParams(
        consumer_tier=consumer_tier,
        consumer_model=consumer_model,
        matched_class_name=matched_class_name,
        tier_profile=tier_profile,
        model_class_profile=model_class_profile,
        exact_model_profile=exact_model_profile,
        effective=effective,
        top_k=max(1, int(effective.get("rag_top_k_per_query", 10))),
        max_chunks=max(1, int(effective.get("rag_max_chunks", 20))),
        rrf_k=max(1, int(effective.get("rag_rrf_k", 35))),
        recency_weight=float(effective.get("rag_recency_weight", 0.2)),
        confidence_threshold=float(effective.get("scope_confidence_threshold", 0.7)),
    )
