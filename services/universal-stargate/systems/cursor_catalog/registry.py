"""
Cursor SDK catalog poller — discovers cursor models via git_integration_worker.

Fetches the model catalog from the worker's ``GET /api/v1/cursor/catalog`` endpoint
at startup and periodically, then registers a virtual ``FederatedGateway`` so
``/v1/models`` and dispatch metadata see the live catalog. Gateways are marked
``dispatchable=False`` so chat/completions routing never forwards to them.

INVARIANT: ∀ gateway mutation: via FederatedGatewayManager.register_cursor_gateway()
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import httpx
from cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    catalog_divergences,
    to_model_card_dict,
)
from model_id import ModelId
from universal_logging import get_logger

from ..federation.common.types import FederatedGateway
from .config import CursorSdkCatalogConfig

if TYPE_CHECKING:
    from ..federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )

logger = get_logger(__name__)

_REFRESH_INTERVAL_S = 3600
_RETRY_INTERVAL_S = 30
_GATEWAY_ID = "cursor-sdk-catalog"


class CursorSdkCatalogPoller:
    """Discover cursor models via git_integration_worker."""

    def __init__(
        self,
        catalog_config: CursorSdkCatalogConfig,
        gateway_manager: FederatedGatewayManager,
        event_bus: object | None = None,
    ) -> None:
        self._worker_url = catalog_config.worker_url.rstrip("/")
        self._gateway_manager = gateway_manager
        self._event_bus = event_bus
        self._client = httpx.AsyncClient(base_url=self._worker_url, timeout=15.0)
        self._refresh_task: asyncio.Task[None] | None = None
        self._connected = False

    async def startup(self) -> None:
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="cursor-sdk-catalog-refresh"
        )
        reachable = await self._probe_health()
        if not reachable:
            await self._gateway_manager.remove_gateways([_GATEWAY_ID])
            logger.warning(
                "Cursor catalog worker not reachable at %s — no models registered",
                self._worker_url,
            )
            await self._emit_unavailable("health probe failed at startup")
        else:
            await self._fetch_and_register()
            self._connected = True
            await self._emit_available()
        logger.info("Cursor SDK catalog poller started")

    async def shutdown(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()
        await self._gateway_manager.remove_gateways([_GATEWAY_ID])
        logger.debug("CursorSdkCatalogPoller shut down")

    async def _probe_health(self) -> bool:
        try:
            response = await self._client.get("/health")
            return response.status_code == 200
        except httpx.HTTPError as exc:
            logger.debug("Cursor catalog worker health probe failed: %s", exc)
            return False

    async def _fetch_catalog(self) -> list[dict[str, Any]]:
        try:
            response = await self._client.get("/api/v1/cursor/catalog")
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", [])
            return models if isinstance(models, list) else []
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch cursor catalog from worker: %s", exc)
            await self._emit_catalog_fetch_failed(str(exc))
            return []

    async def _fetch_and_register(self) -> None:
        catalog = await self._fetch_catalog()
        model_ids: list[ModelId] = []
        model_resources: dict[ModelId, dict[str, Any]] = {}
        projected: dict[str, dict[str, object]] = {}

        for entry in catalog:
            cursor_id = entry.get("cursor_id") or entry.get("id")
            if not isinstance(cursor_id, str) or not cursor_id:
                continue
            if not cursor_id.startswith("cursor/"):
                cursor_id = f"cursor/{cursor_id}"
            mid = ModelId.parse(cursor_id)
            model_ids.append(mid)
            dispatch = entry.get("dispatch")
            if not isinstance(dispatch, dict):
                bare = cursor_id.split("/", 1)[-1]
                capability = CURSOR_MODEL_CAPABILITIES.get(bare)
                dispatch = (
                    to_model_card_dict(capability)
                    if capability is not None
                    else {"knobs": {}, "fixed_params": {}}
                )
            model_resources[mid] = {"dispatch": dispatch, "max_concurrent_requests": 1}
            bare_id = cursor_id.split("/", 1)[-1]
            projected[bare_id] = {
                "knobs": entry.get("knobs", {}),
                "default_variant": entry.get("default_variant", {}),
            }

        if not model_ids:
            await self._gateway_manager.remove_gateways([_GATEWAY_ID])
            return

        gateway = FederatedGateway(
            gateway_id=_GATEWAY_ID,
            remote_stargate_id="cursor-sdk",
            remote_stargate_url=self._worker_url,
            backend_type="cursor_sdk",
            provider_name="cursor",
            available_models=frozenset(model_ids),
            loaded_models=frozenset(model_ids),
            model_resources=model_resources,
            dispatchable=False,
            telemetry_timestamp=time.time(),
            last_heartbeat=time.time(),
        )
        await self._gateway_manager.register_cursor_gateway(gateway)
        await self._emit_catalog_updated(len(model_ids))
        await self._emit_drift_if_needed(projected)

    async def _emit_drift_if_needed(
        self, projected: dict[str, dict[str, object]]
    ) -> None:
        divergences = catalog_divergences(projected)
        if not divergences:
            return
        await self._emit_drift_detected(len(divergences), divergences[:5])

    async def _refresh_loop(self) -> None:
        while True:
            interval_s = (
                _RETRY_INTERVAL_S if not self._connected else _REFRESH_INTERVAL_S
            )
            await asyncio.sleep(interval_s)
            try:
                reachable = await self._probe_health()
                if not reachable:
                    self._connected = False
                    await self._gateway_manager.remove_gateways([_GATEWAY_ID])
                    await self._emit_unavailable("health probe failed during refresh")
                    continue
                was_connected = self._connected
                await self._fetch_and_register()
                self._connected = True
                if not was_connected:
                    await self._emit_available()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error refreshing cursor SDK catalog")

    async def _emit_available(self) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events.cursor_catalog import CursorCatalogAvailable

        gw = self._gateway_manager.get_gateway(_GATEWAY_ID)
        model_count = len(gw.available_models) if gw else 0
        await self._event_bus.publish(
            CursorCatalogAvailable(worker_url=self._worker_url, model_count=model_count)
        )

    async def _emit_unavailable(self, reason: str) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events.cursor_catalog import CursorCatalogUnavailable

        await self._event_bus.publish(
            CursorCatalogUnavailable(worker_url=self._worker_url, reason=reason)
        )

    async def _emit_catalog_updated(self, model_count: int) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events.cursor_catalog import CursorCatalogUpdated

        await self._event_bus.publish(
            CursorCatalogUpdated(worker_url=self._worker_url, model_count=model_count)
        )

    async def _emit_catalog_fetch_failed(self, error: str) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events.cursor_catalog import CursorCatalogFetchFailed

        await self._event_bus.publish(
            CursorCatalogFetchFailed(worker_url=self._worker_url, error=error)
        )

    async def _emit_drift_detected(
        self, divergence_count: int, sample: list[str]
    ) -> None:
        if not self._event_bus:
            return
        from src.scheduling.events.cursor_catalog import CursorCatalogDriftDetected

        await self._event_bus.publish(
            CursorCatalogDriftDetected(
                worker_url=self._worker_url,
                divergence_count=divergence_count,
                sample=sample,
            )
        )
