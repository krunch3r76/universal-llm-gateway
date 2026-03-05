"""
Cloud model catalog manager — fetches and caches provider model lists.

Fetches model catalogs from cloud providers (e.g. OpenRouter) at startup
and periodically, applies prefix filters, and serves the cached result
to Stargate via the /catalog endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import ProviderConfig

logger = logging.getLogger(__name__)


def _per_million(pricing: dict[str, Any], key: str) -> float:
    """Convert per-token string price to per-million-token float."""
    try:
        return round(float(pricing.get(key, "0.0")) * 1_000_000, 4)
    except (ValueError, TypeError):
        return 0.0


@dataclass(slots=True, kw_only=True)
class CatalogModel:
    """A single model entry in the catalog served to Stargate."""

    id: str
    provider: str
    max_concurrent: int
    prompt_cost_per_m: float = 0.0
    completion_cost_per_m: float = 0.0
    context_length: int = 0
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Stargate-facing shape (backward compatible)."""
        return {
            "id": self.id,
            "provider": self.provider,
            "max_concurrent": self.max_concurrent,
        }

    def to_pricing_dict(self) -> dict[str, Any]:
        """Full shape including pricing for cost-aware routing."""
        return {
            "id": self.id,
            "name": self.name or self.id,
            "provider": self.provider,
            "max_concurrent": self.max_concurrent,
            "prompt_cost_per_m": self.prompt_cost_per_m,
            "completion_cost_per_m": self.completion_cost_per_m,
            "context_length": self.context_length,
        }


@dataclass(slots=True, kw_only=True)
class ProviderCatalog:
    """Cached catalog for a single provider."""

    provider: str
    models: list[CatalogModel] = field(default_factory=list)
    base_url: str = ""
    api_key: str = ""


class CatalogManager:
    """Fetches and caches model catalogs from cloud providers.

    Lifecycle:
        1. ``await startup()`` — initial fetch for all providers
        2. Background refresh loop re-fetches periodically
        3. ``await shutdown()`` — cancel refresh + close client
    """

    def __init__(self, providers: list[ProviderConfig]) -> None:
        self._providers = providers
        self._client = httpx.AsyncClient(timeout=30.0)
        self._catalogs: dict[str, ProviderCatalog] = {}
        self._refresh_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        """Fetch initial catalogs from all providers."""
        for provider_config in self._providers:
            await self._fetch_provider(provider_config)

        if self._providers:
            self._refresh_task = asyncio.create_task(
                self._refresh_loop(), name="catalog-refresh"
            )

        total = sum(len(c.models) for c in self._catalogs.values())
        logger.info(
            "Catalog manager started: %d provider(s), %d models total",
            len(self._providers),
            total,
        )

    async def shutdown(self) -> None:
        """Cancel background refresh and close HTTP client."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()
        logger.debug("CatalogManager shut down")

    def get_all_models(self) -> list[dict[str, Any]]:
        """Return the full cached catalog as a list of dicts."""
        result: list[dict[str, Any]] = []
        for catalog in self._catalogs.values():
            result.extend(m.to_dict() for m in catalog.models)
        return result

    def get_all_models_with_pricing(self) -> list[dict[str, Any]]:
        """Return the full catalog with pricing data for cost-aware consumers."""
        result: list[dict[str, Any]] = []
        for catalog in self._catalogs.values():
            result.extend(m.to_pricing_dict() for m in catalog.models)
        return result

    def resolve_provider(self, model_id: str) -> ProviderCatalog | None:
        """Find the provider catalog that contains the given model ID."""
        for catalog in self._catalogs.values():
            if any(m.id == model_id for m in catalog.models):
                return catalog
        return None

    async def _fetch_provider(self, config: ProviderConfig) -> None:
        """Fetch model list from a single provider and cache it."""
        url = f"{config.base_url}/models"
        headers = {"Authorization": f"Bearer {config.api_key}"}

        try:
            response = await self._client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch models from %s: %s", config.provider, exc)
            return

        body: dict[str, Any] = response.json()
        raw_models: list[dict[str, Any]] = body.get("data", [])

        models: list[CatalogModel] = []
        for entry in raw_models:
            mid = entry.get("id", "")
            if not mid or "/" not in mid:
                continue

            if config.allow_prefixes:
                if not any(mid.startswith(p) for p in config.allow_prefixes):
                    continue

            pricing = entry.get("pricing") or {}
            models.append(
                CatalogModel(
                    id=mid,
                    provider=config.provider,
                    max_concurrent=config.max_concurrent,
                    prompt_cost_per_m=_per_million(pricing, "prompt"),
                    completion_cost_per_m=_per_million(pricing, "completion"),
                    context_length=entry.get("context_length", 0),
                    name=entry.get("name", mid),
                )
            )

        self._catalogs[config.provider] = ProviderCatalog(
            provider=config.provider,
            models=models,
            base_url=config.base_url,
            api_key=config.api_key,
        )

        logger.debug(
            "Fetched %d models from %s (%d after prefix filter)",
            len(raw_models),
            config.provider,
            len(models),
        )

    async def _refresh_loop(self) -> None:
        """Periodically re-fetch model lists from all providers."""
        while True:
            min_interval = min(p.refresh_interval_hours for p in self._providers)
            interval_s = max(min_interval * 3600, 600)
            await asyncio.sleep(interval_s)

            for config in self._providers:
                try:
                    await self._fetch_provider(config)
                except Exception:
                    logger.exception(
                        "Error refreshing catalog for '%s'", config.provider
                    )
