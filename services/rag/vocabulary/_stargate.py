"""Stargate model probe: discover a loaded local model for vocabulary classification."""

from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_STARGATE_MODELS_URL = "http://localhost:9999/v1/models"
DEFAULT_STARGATE_CHAT_URL = "http://localhost:9999/v1/chat/completions"


async def pick_loaded_stargate_model(
    client: httpx.AsyncClient,
    *,
    models_url: str = DEFAULT_STARGATE_MODELS_URL,
) -> str | None:
    """Return a gateway-owned model id, or None if Stargate is unreachable."""
    try:
        resp = await client.get(models_url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        logger.warning(
            "Stargate models probe failed due to HTTP error: %s", e, exc_info=True
        )
        return None
    except json.JSONDecodeError as e:
        logger.warning(
            "Stargate models probe failed due to JSON decoding error: %s",
            e,
            exc_info=True,
        )
        return None
    except Exception:
        logger.warning(
            "Stargate models probe failed due to unexpected error", exc_info=True
        )
        return None
    models = data.get("data") or []
    owned_model_ids = [
        m["id"]
        for m in models
        if isinstance(m, dict)
        and isinstance(m.get("id"), str)
        and m["id"]
        and m.get("owned_by") == "universal-llm-gateway"
    ]
    if not owned_model_ids:
        return None

    preferred_prefixes = ("qwen3-5-27b", "qwen3-5-14b", "qwen3-14b", "qwen3")
    for pref in preferred_prefixes:
        for mid in owned_model_ids:
            if pref in mid:
                return mid

    return owned_model_ids[0]
