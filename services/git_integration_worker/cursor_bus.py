"""Agent-bus reply client for cursor-sdk dispatch closeout."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BusReplyResult:
    status_code: int
    body: dict[str, Any] | str


class CursorBusClient:
    """POST ``/turns`` with bearer auth; never raises on transport failure."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_AGENT_BUS_URL,
        token: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._token = (
            token if token is not None else os.environ.get("AGENT_BUS_TOKEN", "")
        ).strip()

    def _headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def _consume_inbox(
        self, client: httpx.AsyncClient, *, thread_id: str, from_agent: str
    ) -> None:
        """Mark unread turns addressed to ``from_agent`` read before replying."""
        await client.get(
            "/turns",
            params={
                "thread": thread_id,
                "to": from_agent,
                "unread": "true",
                "mark_read": "true",
            },
            headers=self._headers(),
        )

    async def _latest_turn_number(
        self, client: httpx.AsyncClient, *, thread_id: str
    ) -> int:
        resp = await client.get(
            "/turns",
            params={"thread": thread_id, "last": 1},
            headers=self._headers(),
        )
        if resp.status_code >= 400:
            return 0
        turns = resp.json().get("turns") or []
        if not turns:
            return 0
        return int(turns[-1]["turn_number"])

    async def reply(
        self,
        *,
        thread_id: str,
        to_agent: str,
        from_agent: str,
        subject: str,
        body: str,
    ) -> BusReplyResult:
        headers = self._headers()
        payload = {
            "thread": thread_id,
            "from": from_agent,
            "to": to_agent,
            "subject": subject,
            "body": body,
            "status": "open",
        }
        try:
            async with make_async_client(self._base_url, timeout=15.0) as client:
                await self._consume_inbox(
                    client, thread_id=thread_id, from_agent=from_agent
                )
                after_turn = await self._latest_turn_number(client, thread_id=thread_id)
                if after_turn:
                    payload["after_turn"] = after_turn
                resp = await client.post("/turns", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.error("cursor bus transport error: %s", exc)
            return BusReplyResult(status_code=599, body=str(exc))
        try:
            parsed: dict[str, Any] | str = resp.json()
        except ValueError:
            parsed = resp.text
        return BusReplyResult(status_code=resp.status_code, body=parsed)

    async def terminate_dispatch(
        self,
        *,
        thread_id: str,
        terminal_status: str,
        execution_id: str | None = None,
    ) -> BusReplyResult:
        payload: dict[str, Any] = {"terminal_status": terminal_status}
        if execution_id is not None:
            payload["execution_id"] = execution_id
        headers = self._headers()
        try:
            async with make_async_client(self._base_url, timeout=15.0) as client:
                resp = await client.post(
                    f"/threads/{thread_id}/dispatch-terminate",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.error("cursor bus terminate transport error: %s", exc)
            return BusReplyResult(status_code=599, body=str(exc))
        try:
            parsed: dict[str, Any] | str = resp.json()
        except ValueError:
            parsed = resp.text
        return BusReplyResult(status_code=resp.status_code, body=parsed)
