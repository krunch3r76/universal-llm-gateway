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
from pathlib import Path
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
_RETRY_INTERVAL_S = 30


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
        self._client = self._make_client(proxy_config.url)
        self._refresh_task: asyncio.Task[None] | None = None
        self._gateway_ids: list[str] = []
        self._connected: bool = False
        self._last_connected_at: float | None = None
        # Extracted once at construction for fast socket-existence checks.
        self._uds_socket_path: Path | None = self._extract_uds_path(proxy_config.url)

    def _make_client(self, url: str) -> httpx.AsyncClient:
        """Create httpx client with UDS or TCP transport."""
        from .forwarder import parse_cloud_proxy_url

        uds_path, base_url = parse_cloud_proxy_url(url)
        if uds_path:
            transport = httpx.AsyncHTTPTransport(uds=uds_path)
            return httpx.AsyncClient(
                transport=transport,
                base_url=base_url,
                timeout=15.0,
            )
        return httpx.AsyncClient(base_url=base_url, timeout=15.0)

    def _extract_uds_path(self, url: str) -> Path | None:
        """Return the UDS socket path if the proxy URL is unix://, else None."""
        from .forwarder import parse_cloud_proxy_url

        uds_path, _ = parse_cloud_proxy_url(url)
        return Path(uds_path) if uds_path else None

    async def startup(self) -> None:
        """Probe proxy health, fetch catalog, register virtual gateways."""
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="cloud-proxy-catalog-refresh"
        )

        reachable, detection_method = await self._probe_health()
        if not reachable:
            logger.warning(
                "Cloud proxy not reachable at %s (%s) — no cloud models registered",
                self._proxy_url,
                detection_method,
            )
            await self._emit_unavailable(
                "health probe failed at startup", detection_method=detection_method
            )
        else:
            await self._fetch_and_register()
            self._connected = True
            self._last_connected_at = time.time()
            await self._emit_available()

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

    async def _probe_health(self) -> tuple[bool, str]:
        """Check if the cloud proxy is reachable.

        Returns (reachable, detection_method) where detection_method is one of:
            'socket_missing'     — UDS socket file absent (no HTTP attempted)
            'health_probe_failed' — HTTP GET /health returned error or timeout
            'ok'                 — probe succeeded
        """
        if self._uds_socket_path is not None and not self._uds_socket_path.exists():
            logger.debug("Cloud proxy UDS socket missing: %s", self._uds_socket_path)
            return False, "socket_missing"
        try:
            response = await self._client.get("/health")
            if response.status_code == 200:
                return True, "ok"
            logger.debug("Cloud proxy health probe returned %d", response.status_code)
            return False, "health_probe_failed"
        except httpx.HTTPError as exc:
            logger.debug("Cloud proxy health probe failed: %s", exc)
            return False, "health_probe_failed"

    async def _fetch_catalog(self) -> list[dict[str, Any]]:
        """Fetch the model catalog from the proxy."""
        try:
            response = await self._client.get("/catalog")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch catalog from cloud proxy: %s", exc)
            await self._emit_catalog_fetch_failed(str(exc))
            return []

    async def _fetch_and_register(self) -> None:
        """Fetch catalog, register virtual gateways grouped by provider.

        Prunes gateways absent from the current catalog so _gateway_ids
        remains an accurate reflection of live cloud providers.
        """
        catalog = await self._fetch_catalog()

        by_provider: dict[str, list[dict[str, Any]]] = {}
        for entry in catalog or []:
            provider = entry.get("provider", "unknown")
            by_provider.setdefault(provider, []).append(entry)

        current_ids: set[str] = set()
        for provider, entries in by_provider.items():
            model_ids: list[ModelId] = []
            max_concurrent = 12
            for entry in entries:
                mid_str = entry.get("id", "")
                if mid_str and "/" in mid_str:
                    model_ids.append(ModelId.parse(mid_str))
                    max_concurrent = entry.get("max_concurrent", max_concurrent)

            if not model_ids:
                continue

            gateway = self._build_virtual_gateway(provider, model_ids, max_concurrent)
            await self._gateway_manager.register_cloud_gateway(gateway)
            current_ids.add(gateway.gateway_id)

            if gateway.gateway_id not in self._gateway_ids:
                self._gateway_ids.append(gateway.gateway_id)

            logger.info(
                "Registered cloud gateway '%s': %d models via proxy",
                gateway.gateway_id,
                len(model_ids),
            )

        # Prune gateways absent from this catalog iteration.
        stale = [gid for gid in self._gateway_ids if gid not in current_ids]
        self._gateway_ids = [gid for gid in self._gateway_ids if gid in current_ids]

        for gid in stale:
            logger.info("Pruned stale cloud gateway '%s' (absent from catalog)", gid)

        await self._emit_catalog_updated(len(catalog or []))

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
            interval_s = (
                _RETRY_INTERVAL_S if not self._connected else _REFRESH_INTERVAL_S
            )
            await asyncio.sleep(interval_s)
            try:
                reachable, detection_method = await self._probe_health()
                if not reachable:
                    self._connected = False
                    await self._emit_unavailable(
                        "health probe failed during refresh",
                        detection_method=detection_method,
                    )
                    continue

                was_connected = self._connected
                await self._fetch_and_register()
                self._connected = True
                self._last_connected_at = time.time()
                if not was_connected:
                    await self._emit_available()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error refreshing cloud proxy catalog")

    # ── Event emission helpers ───────────────────────────────────────────

    async def _emit_available(self) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events import CloudProxyAvailable

        ids_snapshot = list(self._gateway_ids)
        total_models = sum(
            len(gw.available_models)
            for gid in ids_snapshot
            if (gw := self._gateway_manager.get_gateway(gid))
        )
        await self._event_bus.publish_async(
            CloudProxyAvailable(proxy_url=self._proxy_url, model_count=total_models)
        )

    async def _emit_unavailable(
        self, reason: str, *, detection_method: str | None = None
    ) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events import CloudProxyUnavailable

        await self._event_bus.publish_async(
            CloudProxyUnavailable(
                proxy_url=self._proxy_url,
                reason=reason,
                detection_method=detection_method,
            )
        )

    async def _emit_catalog_updated(self, model_count: int) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events import CloudProxyCatalogUpdated

        gateway_count = len(self._gateway_ids)
        await self._event_bus.publish_async(
            CloudProxyCatalogUpdated(
                proxy_url=self._proxy_url,
                model_count=model_count,
                gateway_count=gateway_count,
            )
        )

    async def _emit_catalog_fetch_failed(self, error: str) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events import CloudProxyCatalogFetchFailed

        await self._event_bus.publish_async(
            CloudProxyCatalogFetchFailed(proxy_url=self._proxy_url, error=error)
        )
