"""Cloud-aware model resolution via the cloud proxy's /api/select endpoint.

Handles ``cloud:`` prefixed model_ref values in pipeline steps:
    model_ref: "cloud:code"           → cheapest coding model
    model_ref: "cloud:code,128k"      → coding model with ≥128K context
    model_ref: "cloud:reasoning,5.0"  → reasoning model, ≤$5/M completion

Syntax: cloud:<tag>[,<min_context_k>][,<max_cost>]

Falls back to None (caller should raise or use default) if the cloud
proxy is unavailable or returns no matches.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CLOUD_PROXY_URL = "http://localhost:8200"
_SELECT_TIMEOUT = 3.0


def is_cloud_ref(model_ref: str) -> bool:
    """Check whether a model_ref uses the cloud: prefix syntax."""
    return model_ref.startswith("cloud:")


async def resolve_cloud_ref_async(
    model_ref: str,
    *,
    cloud_proxy_url: str = DEFAULT_CLOUD_PROXY_URL,
) -> tuple[str | None, int]:
    """Resolve a ``cloud:`` prefixed model_ref to a concrete model ID.

    Returns the best matching model ID, or None if unavailable, plus
    candidate_count observed from /api/select.
    """
    spec = model_ref[len("cloud:") :]
    payload = _parse_cloud_spec(spec)
    endpoint = f"{cloud_proxy_url.rstrip('/')}/api/select"

    try:
        async with httpx.AsyncClient(timeout=_SELECT_TIMEOUT) as client:
            resp = await client.post(endpoint, json=payload)
            resp.raise_for_status()
            models = resp.json().get("models", [])
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
    """Parse ``tag[,context_k][,max_cost]`` into a /api/select payload."""
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
