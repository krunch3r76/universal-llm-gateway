"""HTTP helpers for continuity-checkpoint handlers (cortex, agent-bus, Stargate)."""

from __future__ import annotations

import json
import os
from typing import Any

from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    DEFAULT_STARGATE_URL,
    make_async_client,
)
from universal_logging import get_logger

logger = get_logger(__name__)

_HTTP_TIMEOUT_S = 30.0
_CORTEX_TIMEOUT_S = 20.0


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


async def cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """POST cortex-api ``/dispatch`` and return the JSON body or error dict."""
    async with make_async_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT_S) as client:
        try:
            resp = await client.post(
                "/dispatch",
                json={"tool": tool, "arguments": json.dumps(arguments)},
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"transport_error: {exc}"}
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return {"error": f"http_{resp.status_code}: {resp.text[:300]}"}
        return body if isinstance(body, dict) else {"error": str(body)}
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"invalid_json_response: {exc}"}
    return data if isinstance(data, dict) else {"error": "non_object_response"}


async def stargate_post(path: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """POST a Stargate API route and return ``(payload, status_code)``."""
    async with make_async_client(DEFAULT_STARGATE_URL, timeout=_HTTP_TIMEOUT_S) as client:
        try:
            resp = await client.post(path, json=body)
        except Exception as exc:  # noqa: BLE001
            return {"error": {"code": "transport_error", "message": str(exc)}}, 503
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}}
    if not isinstance(payload, dict):
        payload = {"error": {"code": "invalid_response", "message": str(payload)}}
    return payload, resp.status_code


async def bus_get(path: str, *, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    """GET agent-bus REST and return ``(payload, status_code)``."""
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_HTTP_TIMEOUT_S) as client:
        try:
            resp = await client.get(path, params=params or {}, headers=_bus_headers())
        except Exception as exc:  # noqa: BLE001
            return {"error": {"code": "agent_bus_unreachable", "message": str(exc)}}, 503
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}}
    if not isinstance(payload, dict):
        payload = {"error": {"code": "invalid_response", "message": str(payload)}}
    return payload, resp.status_code


async def bus_send(
    *,
    thread: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    after_turn: int | None,
) -> tuple[dict[str, Any], int]:
    """POST ``/threads/send`` and return ``(payload, status_code)``."""
    payload: dict[str, Any] = {
        "thread": thread,
        "from": from_agent,
        "to": to_agent,
        "subject": subject,
        "body": body,
    }
    if after_turn is not None:
        payload["after_turn"] = after_turn
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_HTTP_TIMEOUT_S) as client:
        try:
            resp = await client.post("/threads/send", json=payload, headers=_bus_headers())
        except Exception as exc:  # noqa: BLE001
            return {"error": {"code": "agent_bus_unreachable", "message": str(exc)}}, 503
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}}
    if not isinstance(data, dict):
        data = {"error": {"code": "invalid_response", "message": str(data)}}
    return data, resp.status_code


async def bus_wait(
    *,
    thread: str,
    after_turn: int,
    wait_seconds: float,
    from_agent: str,
) -> tuple[dict[str, Any], int]:
    """GET ``/threads/{thread}/wait`` for pre-consolidate idle polling."""
    params = {
        "after_turn": after_turn,
        "wait": min(wait_seconds, 60.0),
        "completion": "first_reply_from",
        "from_agent": from_agent,
    }
    path = f"/threads/{thread}/wait"
    return await bus_get(path, params=params)


__all__ = ["bus_get", "bus_send", "bus_wait", "cortex_dispatch", "stargate_post"]
