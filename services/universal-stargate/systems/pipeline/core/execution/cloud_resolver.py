"""Cloud-aware model resolution via cloud subsystem /api/select.

Handles ``cloud:`` prefixed model_ref values in pipeline steps:
    model_ref: "cloud:code"           → cheapest coding model
    model_ref: "cloud:code,128k"      → coding model with ≥128K context
    model_ref: "cloud:reasoning,5.0"  → reasoning model, ≤$5/M completion

Syntax: cloud:<tag>[,<min_context_k>][,<max_cost>]

Uses cloud subsystem Python API directly (no HTTP round-trip to self).
Falls back to None (caller should raise or use default) if the cloud
proxy is unavailable or returns no matches.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

CloudSelectFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def is_cloud_ref(model_ref: str) -> bool:
    """Check whether a model_ref uses the cloud: prefix syntax."""
    return model_ref.startswith("cloud:")


async def resolve_cloud_ref_async(
    model_ref: str,
    *,
    cloud_select_fn: CloudSelectFn | None = None,
) -> tuple[str | None, int]:
    """Resolve a ``cloud:`` prefixed model_ref to a concrete model ID.

    Uses cloud_select_fn (from cloud subsystem) when provided. Returns
    the best matching model ID, or None if unavailable, plus candidate_count.
    """
    if not cloud_select_fn:
        logger.warning(
            "cloud resolver: no cloud_select_fn — cloud proxy not configured"
        )
        return None, 0

    spec = model_ref[len("cloud:") :]
    payload = _parse_cloud_spec(spec)

    try:
        result = await cloud_select_fn(payload)
        models = result.get("models", [])
        if models:
            selected = models[0]["id"]
            logger.info(
                "cloud resolver: '%s' → '%s' (from %d candidates)",
                model_ref,
                selected,
                len(models),
            )
            return selected, len(models)
        logger.warning("cloud resolver: no models matched '%s'", model_ref)
        return None, 0
    except Exception as exc:
        logger.warning("cloud resolver unavailable for '%s': %s", model_ref, exc)
        return None, 0


def _parse_cloud_spec(spec: str) -> dict[str, Any]:
    """Parse ``tag[,tag...][,context_k][,max_cost]`` into a /api/select payload."""
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    payload: dict[str, Any] = {"count": 1, "sort_by": "completion_cost"}

    tags: list[str] = []
    for part in parts:
        if part.endswith("k") and part[:-1].isdigit():
            payload["min_context"] = int(part[:-1]) * 1000
        elif _is_float(part):
            payload["max_completion_cost"] = float(part)
        else:
            tags.append(part)

    if tags:
        payload["tags"] = tags
    return payload


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
