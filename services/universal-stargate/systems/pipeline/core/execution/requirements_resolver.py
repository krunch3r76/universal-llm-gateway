"""Resolve model_requirements to concrete model IDs via the profile store.

Called at step init time. Results are cached for the execution lifetime.
Falls back gracefully when the profile store is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def resolve_model_requirements(
    requirements_dict: dict[str, Any],
    proxy: object | None,
    estimated_source_tokens: int | None = None,
) -> list[str]:
    """Resolve a model_requirements dict to a ranked list of model IDs.

    Args:
        requirements_dict: Raw dict from pipeline YAML (ModelRequirements shape).
        proxy: StargateProxy instance (or None in tests).
        estimated_source_tokens: Estimated source token count for the current
            execution. When set, activates large_payload_latency_bucket if the
            count exceeds large_payload_threshold_tokens.

    Returns:
        List of routable model IDs, ordered by suitability.
        Empty list if store is unavailable or no matches found.
    """
    store = _get_store(proxy)
    if store is None:
        logger.warning(
            "Intelligence profile store unavailable, cannot resolve requirements"
        )
        return []

    from intelligence_profiles import ModelRequirements
    from intelligence_profiles.requirements import LATENCY_ORDER

    try:
        requirements = ModelRequirements.model_validate(requirements_dict)
    except Exception:
        logger.exception("Invalid model_requirements: %s", requirements_dict)
        return []

    requirements = _apply_payload_latency_constraint(
        requirements, estimated_source_tokens, LATENCY_ORDER
    )

    model_ids = store.query(requirements)
    if not model_ids:
        logger.warning(
            "No models matched requirements: %s (estimated_tokens=%s)",
            requirements_dict,
            estimated_source_tokens,
        )
    else:
        logger.info(
            "Resolved model_requirements (task=%s, count=%d, max_latency=%s): %s",
            requirements.task,
            len(model_ids),
            requirements.max_latency_bucket,
            model_ids,
        )

    return model_ids


def _apply_payload_latency_constraint(
    requirements: Any,
    estimated_source_tokens: int | None,
    latency_order: dict[str, int],
) -> Any:
    """Inject max_latency_bucket from large_payload rule when threshold exceeded.

    Takes the stricter of the existing max_latency_bucket and the payload-derived
    bucket — lower LATENCY_ORDER value wins.
    """
    req = requirements
    if (
        estimated_source_tokens is None
        or req.large_payload_latency_bucket is None
        or req.large_payload_threshold_tokens is None
        or estimated_source_tokens <= req.large_payload_threshold_tokens
    ):
        return req

    payload_bucket = req.large_payload_latency_bucket
    if req.max_latency_bucket is not None:
        # Keep whichever is stricter (lower order = faster = stricter upper bound)
        if latency_order[req.max_latency_bucket] <= latency_order[payload_bucket]:
            return req  # existing constraint already at least as strict
    logger.info(
        "Large payload (%d tokens > threshold %d): applying max_latency_bucket=%s",
        estimated_source_tokens,
        req.large_payload_threshold_tokens,
        payload_bucket,
    )
    return req.model_copy(update={"max_latency_bucket": payload_bucket})


def _get_store(proxy: object | None) -> object | None:
    """Extract IntelligenceProfileStore from proxy, if available."""
    if proxy is None:
        return None
    return getattr(proxy, "intelligence_profile_store", None)
