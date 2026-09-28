"""Operator-visible delivery for federation liveness stale/recovered signals."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

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

GatewayLivenessPostState = Literal["stale", "recovered"]


def stale_subject(gateway_id: str) -> str:
    return f"liveness stale: {gateway_id}"


def recovered_subject(gateway_id: str) -> str:
    return f"liveness recovered: {gateway_id}"


def parse_gateway_liveness_state(
    subject: str, gateway_id: str
) -> GatewayLivenessPostState | None:
    if subject == stale_subject(gateway_id):
        return "stale"
    if subject == recovered_subject(gateway_id):
        return "recovered"
    return None


def last_gateway_liveness_state_from_turns(
    turns: list[dict[str, Any]],
    *,
    gateway_id: str,
    from_agent: str = _FROM_AGENT,
) -> GatewayLivenessPostState | None:
    """Latest liveness briefing for ``gateway_id`` on the standing thread."""
    ordered = sorted(
        turns,
        key=lambda row: int(row.get("turn_number") or 0),
        reverse=True,
    )
    for turn in ordered:
        author = turn.get("from") or turn.get("from_agent")
        if author != from_agent:
            continue
        state = parse_gateway_liveness_state(str(turn.get("subject") or ""), gateway_id)
        if state is not None:
            return state
    return None


class LivenessAlertBridge:
    """Posts liveness stale/recovered briefings to one standing agent_bus thread.

    Emit decisions compare desired heartbeat state to the last posted turn for
    each ``gateway_id`` on that thread (not process-local memory).
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._standing_thread_id: str | None = None

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

        last = await self._last_posted_state(gateway_id)
        if last == "stale":
            return

        age = payload.get("heartbeat_age_ms")
        threshold = payload.get("threshold_ms")
        last_hb = payload.get("last_heartbeat_iso", "unknown")
        body = (
            f"⚠️ node {gateway_id} silent for {age}ms (>{threshold}ms); "
            f"last heartbeat {last_hb}"
        )
        await self._post_liveness_turn(subject=stale_subject(gateway_id), body=body)

    async def _on_gateway_recovered(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, dict):
            return
        if payload.get("kind") != "liveness":
            return
        gateway_id = payload.get("gateway_id")
        if not isinstance(gateway_id, str) or not gateway_id:
            return

        last = await self._last_posted_state(gateway_id)
        if last != "stale":
            return

        downtime = payload.get("downtime_ms", "unknown")
        body = f"✅ node {gateway_id} heartbeat resumed (downtime {downtime}ms)"
        await self._post_liveness_turn(
            subject=recovered_subject(gateway_id),
            body=body,
        )

    async def _last_posted_state(
        self, gateway_id: str
    ) -> GatewayLivenessPostState | None:
        thread_id = await self._resolve_standing_thread_id()
        if thread_id is None:
            return None
        turns = await self._fetch_recent_turns(thread_id)
        return last_gateway_liveness_state_from_turns(
            turns, gateway_id=gateway_id, from_agent=_FROM_AGENT
        )

    async def _resolve_standing_thread_id(self) -> str | None:
        if self._standing_thread_id is not None:
            return self._standing_thread_id
        found = await self._lookup_thread_id_by_slug(_THREAD_SLUG)
        if found is not None:
            self._standing_thread_id = found
        return self._standing_thread_id

    async def _lookup_thread_id_by_slug(self, slug: str) -> str | None:
        if not self._bus_token_configured():
            return None
        try:
            async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
                response = await client.get(
                    "/threads",
                    params={"query": slug},
                    headers=self._bus_headers(),
                )
            if response.status_code >= 400:
                logger.warning(
                    "Liveness thread lookup failed: status=%s body=%s",
                    response.status_code,
                    response.text[:200],
                )
                return None
            data = response.json()
            threads = data.get("threads") or []
            for row in threads:
                if row.get("slug") == slug:
                    thread_id = row.get("id")
                    if isinstance(thread_id, str) and thread_id:
                        return thread_id
            return None
        except httpx.HTTPError as exc:
            logger.warning("Liveness thread lookup transport error: %s", exc)
            return None

    async def _fetch_recent_turns(self, thread_id: str) -> list[dict[str, Any]]:
        try:
            async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
                response = await client.get(
                    "/turns",
                    params={"thread": thread_id, "last": 100},
                    headers=self._bus_headers(),
                )
            if response.status_code >= 400:
                logger.warning(
                    "Liveness turn history fetch failed: status=%s body=%s",
                    response.status_code,
                    response.text[:200],
                )
                return []
            data = response.json()
            turns = data.get("turns")
            return list(turns) if isinstance(turns, list) else []
        except httpx.HTTPError as exc:
            logger.warning("Liveness turn history transport error: %s", exc)
            return []

    async def _post_liveness_turn(self, *, subject: str, body: str) -> bool:
        if not self._bus_token_configured():
            logger.debug("Agent bus token not configured; skipping liveness alert post")
            return False

        thread_id = await self._resolve_standing_thread_id()
        if thread_id is None:
            return await self._post_new_standing_thread(subject=subject, body=body)
        return await self._post_continue(
            thread_id=thread_id, subject=subject, body=body
        )

    async def _post_new_standing_thread(self, *, subject: str, body: str) -> bool:
        payload: dict[str, Any] = {
            "new_slug": _THREAD_SLUG,
            "from": _FROM_AGENT,
            "to": _TO_AGENT,
            "subject": subject,
            "body": body,
            "status": "open",
        }
        try:
            async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
                response = await client.post(
                    "/threads/send",
                    json=payload,
                    headers=self._bus_headers(),
                )
            if response.status_code == 409:
                detail = response.json().get("detail") or {}
                existing = detail.get("existing_thread_id")
                if isinstance(existing, str) and existing:
                    self._standing_thread_id = existing
                    return await self._post_continue(
                        thread_id=existing, subject=subject, body=body
                    )
                logger.warning(
                    "Liveness standing thread slug collision without id: %s",
                    response.text[:200],
                )
                return False
            if response.status_code >= 400:
                logger.warning(
                    "Liveness standing thread create failed: status=%s body=%s",
                    response.status_code,
                    response.text[:200],
                )
                return False
            data = response.json()
            thread = data.get("thread") or {}
            thread_id = thread.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                logger.warning("Liveness standing thread create missing id: %s", data)
                return False
            self._standing_thread_id = thread_id
            return True
        except httpx.HTTPError as exc:
            logger.warning("Liveness standing thread create transport error: %s", exc)
            return False

    async def _post_continue(self, *, thread_id: str, subject: str, body: str) -> bool:
        payload: dict[str, Any] = {
            "thread": thread_id,
            "from": _FROM_AGENT,
            "to": _TO_AGENT,
            "subject": subject,
            "body": body,
            "status": "open",
        }
        try:
            async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
                response = await client.post(
                    "/threads/send",
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
