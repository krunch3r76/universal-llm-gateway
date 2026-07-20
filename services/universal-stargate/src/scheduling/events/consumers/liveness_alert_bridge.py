"""Operator-visible delivery for federation liveness stale/recovered signals. Subscribes to gateway liveness events on the event bus and posts human-readable alert and recovery briefings to a dedicated agent_bus thread over HTTP, so operators see silent-node and recovery status without polling."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_event_bus import Event
from universal_logging import get_logger

from src.scheduling.events.federation_signaling import (
    FEDERATION_GATEWAY_LIVENESS_STALE,
    FEDERATION_GATEWAY_RECOVERED,
)

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)

_THREAD_SLUG = "federation-liveness-alerts"
_FROM_AGENT = "stargate-liveness-watchdog"
_TO_AGENT = "claude-web"


class LivenessAlertBridge:
    """Posts liveness stale/recovered briefings to a dedicated agent_bus thread. Subscribes to FEDERATION_GATEWAY_LIVENESS_STALE and FEDERATION_GATEWAY_RECOVERED events, tracks open alerts in `_open_alerts` to avoid duplicate stale notices, and skips posting entirely when no agent-bus token is configured."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._open_alerts: set[str] = set()

    def start(self) -> None:
        self._event_bus.subscribe_async(
            FEDERATION_GATEWAY_LIVENESS_STALE,
            self._on_liveness_stale,
        )
        self._event_bus.subscribe_async(
            FEDERATION_GATEWAY_RECOVERED,
            self._on_gateway_recovered,
        )
        logger.info(
            "✅ LivenessAlertBridge subscribed (%s, recovered kind=liveness)",
            FEDERATION_GATEWAY_LIVENESS_STALE,
        )

    async def _on_liveness_stale(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, dict):
            return
        gateway_id = payload.get("gateway_id")
        if not isinstance(gateway_id, str) or not gateway_id:
            return
        if gateway_id in self._open_alerts:
            return

        age = payload.get("heartbeat_age_ms")
        threshold = payload.get("threshold_ms")
        last_hb = payload.get("last_heartbeat_iso", "unknown")
        body = (
            f"⚠️ node {gateway_id} silent for {age}ms (>{threshold}ms); "
            f"last heartbeat {last_hb}"
        )
        if await self._post_turn(
            subject=f"liveness stale: {gateway_id}",
            body=body,
        ):
            self._open_alerts.add(gateway_id)

    async def _on_gateway_recovered(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, dict):
            return
        if payload.get("kind") != "liveness":
            return
        gateway_id = payload.get("gateway_id")
        if not isinstance(gateway_id, str) or not gateway_id:
            return

        downtime = payload.get("downtime_ms", "unknown")
        body = f"✅ node {gateway_id} heartbeat resumed (downtime {downtime}ms)"
        if await self._post_turn(
            subject=f"liveness recovered: {gateway_id}",
            body=body,
        ):
            self._open_alerts.discard(gateway_id)

    async def _post_turn(self, *, subject: str, body: str) -> bool:
        if not self._bus_token_configured():
            logger.debug("Agent bus token not configured; skipping liveness alert post")
            return False

        payload: dict[str, Any] = {
            "thread": _THREAD_SLUG,
            "from": _FROM_AGENT,
            "to": _TO_AGENT,
            "subject": subject,
            "body": body,
            "status": "open",
        }
        try:
            async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
                response = await client.post(
                    "/turns",
                    json=payload,
                    headers=self._bus_headers(),
                )
            if response.status_code >= 400:
                logger.warning(
                    "Liveness alert post failed: status=%s body=%s",
                    response.status_code,
                    response.text[:200],
                )
                return False
            return True
        except httpx.HTTPError as exc:
            logger.warning("Liveness alert post transport error: %s", exc)
            return False

    @staticmethod
    def _bus_headers() -> dict[str, str]:
        token = os.getenv("AGENT_BUS_TOKEN", "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    @staticmethod
    def _bus_token_configured() -> bool:
        if os.getenv("AGENT_BUS_TOKEN", "").strip():
            return True
        return os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
