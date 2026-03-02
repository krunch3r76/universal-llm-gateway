"""Cloud proxy model selection for pipeline_test tools.

Queries the cloud proxy /api/select endpoint to dynamically choose
models based on capability tags and context requirements.  Falls back
to caller-supplied defaults when the proxy is unreachable.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

CLOUD_PROXY_URL = os.getenv("CLOUD_PROXY_URL", "http://localhost:8200").rstrip("/")


def select_models(
    *,
    tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    min_context: int = 0,
    count: int = 2,
    timeout: float = 5.0,
) -> list[str] | None:
    """Query cloud proxy for models matching capability constraints.

    Returns model IDs on success, None when the proxy is unavailable
    or returns no results (caller should fall back to defaults).
    """
    payload: dict[str, object] = {"count": count}
    if tags:
        payload["tags"] = tags
    if exclude_tags:
        payload["exclude_tags"] = exclude_tags
    if min_context:
        payload["min_context"] = min_context

    try:
        resp = httpx.post(
            f"{CLOUD_PROXY_URL}/api/select",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        models = [m["id"] for m in resp.json().get("models", [])]
        if models:
            return models
    except Exception as exc:
        logger.debug("Cloud proxy select unavailable: %s", exc)
    return None
