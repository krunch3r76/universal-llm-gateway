"""
Cloud model catalog manager — fetches and caches provider model lists.

Fetches model catalogs from cloud providers (e.g. OpenRouter) at startup
and periodically, applies prefix filters, and serves the cached result
to Stargate via the /catalog endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .adapters.base import ProviderAdapter
from .config import ProviderConfig

logger = logging.getLogger(__name__)

_STARTUP_FETCH_ATTEMPTS = 3
_STARTUP_FETCH_BASE_DELAY_S = 1.0


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
    adapter_type: str = ""


class CatalogManager:
    """Fetches and caches model catalogs from cloud providers.

    Lifecycle:
        1. ``await startup()`` — initial fetch for all providers
        2. Background refresh loop re-fetches periodically
        3. ``await shutdown()`` — cancel refresh loop
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        adapters: dict[str, ProviderAdapter],
        on_provider_catalog_refreshed: Callable[[str, int], Awaitable[None]]
        | None = None,
        on_provider_catalog_refresh_failed: Callable[[str, str], Awaitable[None]]
        | None = None,
    ) -> None:
        self._providers = providers
        self._adapters = adapters
        self._on_provider_catalog_refreshed = on_provider_catalog_refreshed
        self._on_provider_catalog_refresh_failed = on_provider_catalog_refresh_failed
        self._catalogs: dict[str, ProviderCatalog] = {}
        self._refresh_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        """Fetch initial catalogs from all providers."""
        for provider_config in self._providers:
            await self._fetch_provider_with_startup_retries(provider_config)

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
        """Cancel background refresh task."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        logger.debug("CatalogManager shut down")

    def get_all_models(self) -> list[dict[str, Any]]:
        """Return the full cached catalog as a list of dicts.

        Each model also gets a ``{id}-mcp`` synthetic variant that triggers
        MCP tool injection when used via ``/v1/chat/completions``.

        ``-mcp`` variants are for **agentic clients only** — callers that
        implement the tool-call execution loop (dispatch tool calls to the MCP
        server, append role=tool results, re-submit).  Chat UIs such as
        OpenWebUI must use the bare model ID; they will receive a broken
        ``finish_reason="tool_calls"`` response and stall otherwise.
        """
        _mcp_meta: dict[str, Any] = {
            "description": (
                "Agentic tool-call variant — requires client-side tool "
                "execution loop. Not for chat UIs."
            ),
            "tags": ["agentic"],
        }
        result: list[dict[str, Any]] = []
        for catalog in self._catalogs.values():
            for m in catalog.models:
                result.append(m.to_dict())
                result.append({**m.to_dict(), "id": f"{m.id}-mcp", **_mcp_meta})
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
            for model in catalog.models:
                if model.id == model_id:
                    return catalog
        return None

    async def _fetch_provider(self, config: ProviderConfig) -> bool:
        """Fetch model list from a single provider and cache it."""
        adapter = self._adapters.get(config.provider)
        if adapter is None:
            logger.error("No adapter for provider '%s'", config.provider)
            if self._on_provider_catalog_refresh_failed is not None:
                await self._on_provider_catalog_refresh_failed(
                    config.provider, "No adapter configured"
                )
            return False

        try:
            raw_models = await adapter.fetch_catalog()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch models from %s: %s", config.provider, exc)
            if self._on_provider_catalog_refresh_failed is not None:
                await self._on_provider_catalog_refresh_failed(
                    config.provider, str(exc)
                )
            return False

        models: list[CatalogModel] = []
        for entry in raw_models:
            raw_mid = str(entry.get("id", "")).strip()
            if not raw_mid:
                continue
            mid = adapter.normalize_catalog_model_id(raw_mid)

            if config.allow_prefixes:
                if not any(mid.startswith(p) for p in config.allow_prefixes):
                    continue

            pricing = entry.get("pricing") or {}
            base_model = CatalogModel(
                id=mid,
                provider=config.provider,
                max_concurrent=config.max_concurrent,
                prompt_cost_per_m=_per_million(pricing, "prompt"),
                completion_cost_per_m=_per_million(pricing, "completion"),
                context_length=int(
                    entry.get("context_length", entry.get("max_context_tokens", 0)) or 0
                ),
                name=str(entry.get("name", entry.get("display_name", mid))),
            )
            models.append(base_model)

        self._catalogs[config.provider] = ProviderCatalog(
            provider=config.provider,
            models=models,
            base_url=config.base_url,
            api_key=config.api_key,
            adapter_type=adapter.adapter_type,
        )
        if self._on_provider_catalog_refreshed is not None:
            await self._on_provider_catalog_refreshed(config.provider, len(models))

        logger.debug(
            "Fetched %d models from %s (%d after prefix filter)",
            len(raw_models),
            config.provider,
            len(models),
        )
        return True

    async def _fetch_provider_with_startup_retries(
        self, config: ProviderConfig
    ) -> None:
        """Retry transient startup fetch failures before leaving a provider empty."""
        for attempt in range(1, _STARTUP_FETCH_ATTEMPTS + 1):
            if await self._fetch_provider(config):
                return

            if attempt == _STARTUP_FETCH_ATTEMPTS:
                return

            delay_s = _STARTUP_FETCH_BASE_DELAY_S * (2 ** (attempt - 1))
            logger.warning(
                "Catalog fetch for provider '%s' failed at startup "
                "(attempt %d/%d); retrying in %.1fs",
                config.provider,
                attempt,
                _STARTUP_FETCH_ATTEMPTS,
                delay_s,
            )
            await asyncio.sleep(delay_s)

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
