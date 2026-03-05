"""Browser catalog cache — full OpenRouter model listing with pricing.

Fetches the public /api/v1/models endpoint (no API key needed) and
normalizes pricing to per-million-token floats.  Separate from the
provider-filtered CatalogManager: this shows ALL models for browsing,
while CatalogManager shows only the configured subset for routing.

Also provides capability tagging and model selection for task-aware
routing: given a task type + constraints, return the best-suited models.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from .tagging import derive_tags, derive_tier

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL_S = 900  # 15 minutes


# ── Cache ───────────────────────────────────────────────────────────


class BrowserCatalogCache:
    """In-memory cache for the full OpenRouter catalog with pricing and tags."""

    def __init__(self) -> None:
        self._models: list[dict[str, Any]] = []
        self._last_refresh: float = 0.0

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self._last_refresh) > CACHE_TTL_S

    @property
    def model_count(self) -> int:
        return len(self._models)

    async def refresh(self) -> int:
        """Fetch models from the public OpenRouter API. Returns model count."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(OPENROUTER_MODELS_URL)
            response.raise_for_status()

        raw_models: list[dict[str, Any]] = response.json().get("data", [])
        self._models = [_process_model(m) for m in raw_models]
        self._last_refresh = time.monotonic()

        logger.info("Browser catalog refreshed: %d models", len(self._models))
        return len(self._models)

    async def ensure_fresh(self) -> None:
        """Refresh if cache is stale."""
        if self.is_stale:
            await self.refresh()

    def get_models(self) -> list[dict[str, Any]]:
        return self._models

    def lookup(self, model_id: str) -> dict[str, Any] | None:
        """Look up a single model by ID."""
        for m in self._models:
            if m["id"] == model_id:
                return m
        return None

    def select(
        self,
        *,
        tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        min_context: int = 0,
        min_prompt_cost: float | None = None,
        max_prompt_cost: float | None = None,
        min_completion_cost: float | None = None,
        max_completion_cost: float | None = None,
        modality_contains: str | None = None,
        providers: list[str] | None = None,
        count: int = 3,
        sort_by: str = "tier",
        extra_models: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter and rank models by capability, cost, and context constraints.

        Default sort is ``"tier"`` — highest quality tier first, randomized
        within the same tier.  Use ``sort_by="completion_cost"`` for
        deterministic cost-ascending ordering.

        Multimodal models (vision/audio/video) are included by default —
        multimodal capability does not diminish text quality. To exclude them,
        pass ``exclude_tags=["vision", "audio", "video"]`` explicitly.

        Local models (source="local", cost=0) bypass min cost filters —
        those filters target free-tier cloud junk, not local resources.
        """
        candidates = self._models + (extra_models or [])
        _tags = set(tags) if tags else set()
        _excl = set(exclude_tags) if exclude_tags else set()

        results: list[dict[str, Any]] = []
        for m in candidates:
            model_tags = set(m.get("tags", []))
            if _tags and not _tags & model_tags:
                continue
            if _excl and _excl & model_tags:
                continue
            if m.get("context_length", 0) < min_context:
                continue

            is_free_local = m.get("source") == "local" and m["prompt_cost"] == 0

            if min_prompt_cost is not None and m["prompt_cost"] < min_prompt_cost:
                if not is_free_local:
                    continue
            if max_prompt_cost is not None and m["prompt_cost"] > max_prompt_cost:
                continue
            if (
                min_completion_cost is not None
                and m["completion_cost"] < min_completion_cost
            ):
                if not is_free_local:
                    continue
            if (
                max_completion_cost is not None
                and m["completion_cost"] > max_completion_cost
            ):
                continue
            if modality_contains and modality_contains not in m.get("modality", ""):
                continue
            if providers and m.get("provider", "") not in providers:
                continue
            results.append(m)

        if sort_by == "tier":
            results.sort(key=lambda m: (-m.get("tier", 0), random.random()))
        else:
            results.sort(key=lambda m: m.get(sort_by, 0))
        return results[:count]


# ── Processing ──────────────────────────────────────────────────────


def _per_million(pricing: dict[str, Any], key: str) -> float:
    """Convert per-token string price to per-million-token float."""
    try:
        return round(float(pricing.get(key, "0.0")) * 1_000_000, 4)
    except (ValueError, TypeError):
        return 0.0


def _process_model(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw OpenRouter model entry.

    Pricing is converted from per-token strings to per-million-token floats.
    Provider is extracted from the model ID (format: provider/model-name).
    Capability tags are derived from model ID patterns and modality.
    """
    pricing = raw.get("pricing") or {}
    model_id = raw.get("id", "")
    provider = model_id.split("/", 1)[0] if "/" in model_id else ""
    raw_architecture = raw.get("architecture") or {}
    modality = raw_architecture.get("modality", "")
    completion_cost = _per_million(pricing, "completion")
    tags = derive_tags(model_id, modality)

    return {
        "id": model_id,
        "name": raw.get("name", model_id),
        "provider": provider,
        "context_length": raw.get("context_length", 0),
        "prompt_cost": _per_million(pricing, "prompt"),
        "completion_cost": completion_cost,
        "image_cost": _per_million(pricing, "image"),
        "request_cost": _per_million(pricing, "request"),
        "modality": modality,
        "description": raw.get("description", ""),
        "architecture": {
            "input_modalities": raw_architecture.get("input_modalities", []),
            "output_modalities": raw_architecture.get("output_modalities", []),
            "tokenizer": raw_architecture.get("tokenizer", ""),
            "instruct_type": raw_architecture.get("instruct_type", ""),
        },
        "supported_parameters": raw.get("supported_parameters") or [],
        "top_provider": raw.get("top_provider") or {},
        "created": raw.get("created"),
        "tags": tags,
        "tier": derive_tier(model_id, completion_cost, "cloud"),
        "source": "cloud",
    }
