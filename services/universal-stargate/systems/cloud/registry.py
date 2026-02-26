"""
Cloud proxy catalog poller — discovers cloud models via the proxy.

Fetches the model catalog from the cloud proxy's ``/catalog`` endpoint
at startup and periodically, then registers them as virtual
``FederatedGateway`` entries so existing routing infrastructure treats
them uniformly.

INVARIANT: ∀ gateway mutation: via FederatedGatewayManager.register_cloud_gateway()
           (never direct ``_gateways`` access)
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import httpx
from model_id import ModelId
from universal_logging import get_logger

from ..federation.common.types import FederatedGateway
from .config import CloudProxyConfig

if TYPE_CHECKING:
    from ..federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )

logger = get_logger(__name__)

_REFRESH_INTERVAL_S = 3600
_MIN_REFRESH_S = 600


class CloudProxyCatalogPoller:
    """Discover cloud models via the proxy and register virtual gateways.

    All gateway mutations go through
    ``FederatedGatewayManager.register_cloud_gateway()`` which handles
    sequential execution, capacity pool seeding, and catalog-change events.

    Lifecycle:
        1. ``__init__`` with proxy config + gateway manager + event bus
        2. ``await startup()`` — initial catalog fetch + registration
        3. Background refresh loop re-fetches periodically
        4. ``await shutdown()`` — cancel refresh + close client
    """

    def __init__(
        self,
        proxy_config: CloudProxyConfig,
        gateway_manager: FederatedGatewayManager,
        event_bus: object | None = None,
    ) -> None:
        self._proxy_url = proxy_config.url.rstrip("/")
        self._gateway_manager = gateway_manager
        self._event_bus = event_bus
        self._client = httpx.AsyncClient(timeout=15.0)
        self._refresh_task: asyncio.Task[None] | None = None
        self._gateway_ids: list[str] = []

    async def startup(self) -> None:
        """Probe proxy health, fetch catalog, register virtual gateways."""
        if not await self._probe_health():
            logger.warning(
                "Cloud proxy not reachable at %s — no cloud models registered",
                self._proxy_url,
            )
            await self._emit_unavailable("health probe failed at startup")
            return

        await self._fetch_and_register()
        await self._emit_available()

        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="cloud-proxy-catalog-refresh"
        )
        logger.info(
            "Cloud proxy catalog poller started: %d virtual gateway(s)",
            len(self._gateway_ids),
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
        logger.debug("CloudProxyCatalogPoller shut down")

    async def _probe_health(self) -> bool:
        """Check if the cloud proxy is reachable."""
        try:
            response = await self._client.get(f"{self._proxy_url}/health")
            return response.status_code == 200
        except httpx.HTTPError as exc:
            logger.debug("Cloud proxy health probe failed: %s", exc)
            return False

    async def _fetch_catalog(self) -> list[dict[str, Any]]:
        """Fetch the model catalog from the proxy."""
        try:
            response = await self._client.get(f"{self._proxy_url}/catalog")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch catalog from cloud proxy: %s", exc)
            await self._emit_catalog_fetch_failed(str(exc))
            return []

    async def _fetch_and_register(self) -> None:
        """Fetch catalog and register virtual gateways grouped by provider."""
        catalog = await self._fetch_catalog()
        if not catalog:
            return

        by_provider: dict[str, list[dict[str, Any]]] = {}
        for entry in catalog:
            provider = entry.get("provider", "unknown")
            by_provider.setdefault(provider, []).append(entry)

        for provider, entries in by_provider.items():
            model_ids: list[ModelId] = []
            max_concurrent = 5
            for entry in entries:
                mid_str = entry.get("id", "")
                if mid_str and "/" in mid_str:
                    model_ids.append(ModelId.parse(mid_str))
                    max_concurrent = entry.get("max_concurrent", max_concurrent)

            if not model_ids:
                continue

            gateway = self._build_virtual_gateway(provider, model_ids, max_concurrent)
            await self._gateway_manager.register_cloud_gateway(gateway)

            if gateway.gateway_id not in self._gateway_ids:
                self._gateway_ids.append(gateway.gateway_id)

            logger.info(
                "Registered cloud gateway '%s': %d models via proxy",
                gateway.gateway_id,
                len(model_ids),
            )

        await self._emit_catalog_updated(len(catalog))

    def _build_virtual_gateway(
        self,
        provider: str,
        model_ids: list[ModelId],
        max_concurrent: int,
    ) -> FederatedGateway:
        """Create a FederatedGateway representing a cloud provider via proxy."""
        gateway_id = f"cloud-{provider}"
        models_frozenset = frozenset(model_ids)

        model_resources: dict[ModelId, dict[str, int | str]] = {
            mid: {"max_concurrent_requests": max_concurrent} for mid in model_ids
        }

        return FederatedGateway(
            gateway_id=gateway_id,
            remote_stargate_id=f"cloud-{provider}",
            remote_stargate_url=self._proxy_url,
            backend_type="cloud_api",
            provider_url=self._proxy_url,
            provider_name=provider,
            available_models=models_frozenset,
            loaded_models=models_frozenset,
            model_resources=model_resources,
            telemetry_timestamp=time.time(),
            last_heartbeat=time.time(),
        )

    async def _refresh_loop(self) -> None:
        """Periodically re-fetch the catalog from the proxy."""
        while True:
            await asyncio.sleep(max(_REFRESH_INTERVAL_S, _MIN_REFRESH_S))
            try:
                if not await self._probe_health():
                    await self._emit_unavailable("health probe failed during refresh")
                    continue
                await self._fetch_and_register()
            except Exception:
                logger.exception("Error refreshing cloud proxy catalog")

    # ── Event emission helpers ───────────────────────────────────────────

    async def _emit_available(self) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events import CloudProxyAvailable

        total_models = sum(
            len(gw.available_models)
            for gid in self._gateway_ids
            if (gw := self._gateway_manager.get_gateway(gid))
        )
        await self._event_bus.publish_async(
            CloudProxyAvailable(proxy_url=self._proxy_url, model_count=total_models)
        )

    async def _emit_unavailable(self, reason: str) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events import CloudProxyUnavailable

        await self._event_bus.publish_async(
            CloudProxyUnavailable(proxy_url=self._proxy_url, reason=reason)
        )

    async def _emit_catalog_updated(self, model_count: int) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events import CloudProxyCatalogUpdated

        await self._event_bus.publish_async(
            CloudProxyCatalogUpdated(
                proxy_url=self._proxy_url,
                model_count=model_count,
                gateway_count=len(self._gateway_ids),
            )
        )

    async def _emit_catalog_fetch_failed(self, error: str) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events import CloudProxyCatalogFetchFailed

        await self._event_bus.publish_async(
            CloudProxyCatalogFetchFailed(proxy_url=self._proxy_url, error=error)
        )
