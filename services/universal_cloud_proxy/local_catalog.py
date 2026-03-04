"""Local model catalog cache — discovers models from Stargate /v1/models.

Polls the Stargate endpoint periodically and normalizes entries into the
same dict shape as BrowserCatalogCache (id, tags, costs, context_length,
source). Local models have zero cost, making them naturally preferred in
cost-sorted selection results.
"""

from __future__ import annotations

import asyncio
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
        self._refresh_lock = asyncio.Lock()

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

        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            raise ValueError("Invalid /v1/models payload: data must be a list")
        raw_models: list[dict[str, Any]] = [m for m in data if isinstance(m, dict)]
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
        if not self.is_stale:
            return

        async with self._refresh_lock:
            if not self.is_stale:
                return
            try:
                await self.refresh()
            except Exception as exc:
                logger.warning("Local catalog refresh failed: %s", exc, exc_info=True)

    def get_models(self) -> list[dict[str, Any]]:
        return [model.copy() for model in self._models]


def _process_local_model(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Stargate /v1/models entry to the unified selection shape."""
    model_id = raw.get("id", "")
    context_length = raw.get("context_length", 0) or raw.get(
        "effective_context_per_slot", 0
    )

    modality = ""
    capabilities: dict[str, Any] | None = raw.get("capabilities") or None
    if isinstance(capabilities, dict):
        modalities = capabilities.get("modalities", {})
        if isinstance(modalities, dict):
            input_modalities_raw = modalities.get("input", [])
            output_modalities_raw = modalities.get("output", [])

            input_modalities = (
                input_modalities_raw if isinstance(input_modalities_raw, list) else []
            )
            output_modalities = (
                output_modalities_raw if isinstance(output_modalities_raw, list) else []
            )

            all_modalities = {
                value
                for value in [*input_modalities, *output_modalities]
                if isinstance(value, str)
            }
            all_modalities.discard("text")
            modality = ",".join(sorted(all_modalities))

    tags = sorted({*derive_tags(model_id, modality), "local"})

    return {
        "id": model_id,
        "name": model_id,
        "provider": "local",
        "context_length": context_length,
        "prompt_cost": 0.0,
        "completion_cost": 0.0,
        "image_cost": 0.0,
        "request_cost": 0.0,
        "modality": modality,
        "description": "",
        "tags": tags,
        "tier": derive_tier(model_id, 0.0, "local"),
        "source": "local",
        "capabilities": capabilities,
    }
