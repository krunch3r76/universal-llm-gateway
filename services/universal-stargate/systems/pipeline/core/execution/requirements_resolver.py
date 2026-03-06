"""Resolve model_requirements to concrete model IDs via POST /v1/models/select.

Called at step init time. Results are cached for the execution lifetime.
Falls back gracefully when the endpoint is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5.0
_LOOPBACK_URL = "http://localhost:9999"


def resolve_model_requirements(
    requirements_dict: dict[str, Any],
    estimated_source_tokens: int | None = None,
) -> list[str]:
    """Resolve a model_requirements dict to a ranked list of model IDs.

    Args:
        requirements_dict: Raw dict from pipeline YAML (SelectionRequest shape).
        estimated_source_tokens: When set, activates large_payload_latency_bucket
            constraint if the count exceeds large_payload_threshold_tokens.
            Applied as a pre-call transform before forwarding to the endpoint.

    Returns:
        List of routable model IDs, ordered by suitability.
        Empty list if endpoint is unavailable or no matches found.
    """
    payload = _apply_payload_latency_constraint(
        dict(requirements_dict), estimated_source_tokens
    )

    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client:
            resp = client.post(f"{_LOOPBACK_URL}/v1/models/select", json=payload)
        resp.raise_for_status()
        data = resp.json()
        model_ids = [m["id"] for m in data.get("models", []) if isinstance(m, dict)]
        if not model_ids:
            logger.warning(
                "No models matched requirements: %s (path=%s)",
                requirements_dict,
                data.get("selection_path"),
            )
        else:
            logger.info(
                "Resolved requirements (task=%s path=%s): %s",
                requirements_dict.get("task"),
                data.get("selection_path"),
                model_ids,
            )
        return model_ids
    except httpx.HTTPStatusError as exc:
        logger.error(
            "model_requirements resolution HTTP %d: %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.error("model_requirements resolution network error: %s", exc)
    except Exception as exc:
        logger.exception("model_requirements resolution unexpected error: %s", exc)
    return []


def _apply_payload_latency_constraint(
    payload: dict[str, Any],
    estimated_source_tokens: int | None,
) -> dict[str, Any]:
    """Inject max_latency_bucket from large_payload rule when threshold exceeded.

    Keeps this constraint in the pipeline layer — it's a pipeline concern
    (token estimation), not a selection concern. The endpoint receives the
    already-tightened max_latency_bucket and enforces it normally.
    """
    if estimated_source_tokens is None:
        return payload
    large_bucket = payload.get("large_payload_latency_bucket")
    threshold = payload.get("large_payload_threshold_tokens")
    if large_bucket is None or threshold is None:
        return payload
    if estimated_source_tokens <= threshold:
        return payload

    from intelligence_profiles.requirements import LATENCY_ORDER

    existing = payload.get("max_latency_bucket")
    existing_order = LATENCY_ORDER.get(existing, 99) if existing else 99
    if existing is None or LATENCY_ORDER.get(large_bucket, 99) < existing_order:
        logger.info(
            "Large payload (%d tokens > threshold %d): applying max_latency_bucket=%s",
            estimated_source_tokens,
            threshold,
            large_bucket,
        )
        payload = {**payload, "max_latency_bucket": large_bucket}
    return payload
