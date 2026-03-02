"""Local model catalog cache — discovers models from Stargate /v1/models.

Polls the Stargate endpoint periodically and normalizes entries into the
same dict shape as BrowserCatalogCache (id, tags, costs, context_length,
source). Local models have zero cost, making them naturally preferred in
cost-sorted selection results.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .tagging import derive_tags, derive_tier

logger = logging.getLogger(__name__)

_DEFAULT_STARGATE_URL = "http://localhost:9999"
CACHE_TTL_S = 60


class LocalCatalogCache:
    """In-memory cache for locally available models from Stargate."""

    def __init__(self, stargate_url: str = _DEFAULT_STARGATE_URL) -> None:
        self._stargate_url = stargate_url.rstrip("/")
        self._models: list[dict[str, Any]] = []
        self._last_refresh: float = 0.0

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self._last_refresh) > CACHE_TTL_S

    @property
    def model_count(self) -> int:
        return len(self._models)

    async def refresh(self) -> int:
        """Fetch models from Stargate /v1/models. Returns model count."""
        url = f"{self._stargate_url}/v1/models"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params={"include_metadata": "true"})
            response.raise_for_status()

        raw_models: list[dict[str, Any]] = response.json().get("data", [])
        self._models = [
            _process_local_model(m)
            for m in raw_models
            if m.get("id") and m.get("type") == "model"
        ]
        self._last_refresh = time.monotonic()
        logger.info(
            "Local catalog refreshed: %d models from %s",
            len(self._models),
            self._stargate_url,
        )
        return len(self._models)

    async def ensure_fresh(self) -> None:
        """Refresh if cache is stale."""
        if self.is_stale:
            try:
                await self.refresh()
            except Exception as exc:
                logger.debug("Local catalog refresh failed: %s", exc)

    def get_models(self) -> list[dict[str, Any]]:
        return self._models


def _process_local_model(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Stargate /v1/models entry to the unified selection shape."""
    model_id = raw.get("id", "")
    context_length = raw.get("context_length", 0) or raw.get(
        "effective_context_per_slot", 0
    )
    tags = derive_tags(model_id)

    return {
        "id": model_id,
        "name": model_id,
        "provider": "local",
        "context_length": context_length,
        "prompt_cost": 0.0,
        "completion_cost": 0.0,
        "image_cost": 0.0,
        "request_cost": 0.0,
        "modality": "",
        "description": "",
        "tags": tags,
        "tier": derive_tier(model_id, 0.0, "local"),
        "source": "local",
    }
