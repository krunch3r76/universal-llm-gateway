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

    async def reply(
        self,
        *,
        thread_id: str,
        to_agent: str,
        from_agent: str,
        subject: str,
        body: str,
    ) -> BusReplyResult:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        payload = {
            "thread": thread_id,
            "from": from_agent,
            "to": to_agent,
            "subject": subject,
            "body": body,
            "status": "open",
            "after_turn": 0,
        }
        try:
            async with make_async_client(self._base_url, timeout=15.0) as client:
                resp = await client.post("/turns", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.error("cursor bus transport error: %s", exc)
            return BusReplyResult(status_code=599, body=str(exc))
        try:
            parsed: dict[str, Any] | str = resp.json()
        except ValueError:
            parsed = resp.text
        return BusReplyResult(status_code=resp.status_code, body=parsed)
