"""HTTP helpers for continuity-checkpoint handlers (cortex, agent-bus, Stargate)."""

from __future__ import annotations

import os
from typing import Any

from continuity_tape.checkpoint_http import (
    bus_get,
    bus_send,
    cortex_dispatch,
    step_output_json,
)
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_HTTP_TIMEOUT_S = 30.0
_CDP_ASK_TIMEOUT_S = 90.0


async def cdp_ask_harvest(
    chat_url: str,
    *,
    limit: int = 50,
    source: str = "chat",
) -> dict[str, Any]:
    """POST cdp_ask ``/v1/cse-session/harvest`` and return parsed JSON."""
    base = os.getenv("CDP_ASK_URL", "http://jupiter:8770").rstrip("/")
    payload = {"chat_url": chat_url, "limit": limit, "source": source}
    async with make_async_client(base, timeout=_CDP_ASK_TIMEOUT_S) as client:
        try:
            resp = await client.post("/v1/cse-session/harvest", json=payload)
        except Exception as exc:  # noqa: BLE001
            return {"outcome": "error", "reason": str(exc)}
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "error", "reason": f"invalid_json: {exc}"}
    return (
        data
        if isinstance(data, dict)
        else {"outcome": "error", "reason": "non_object_response"}
    )


async def stargate_post(path: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """POST a Stargate API route and return ``(payload, status_code)``."""
    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_HTTP_TIMEOUT_S
    ) as client:
        try:
            resp = await client.post(path, json=body)
        except Exception as exc:  # noqa: BLE001
            return {"error": {"code": "transport_error", "message": str(exc)}}, 503
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {
            "error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}
        }
    if not isinstance(payload, dict):
        payload = {"error": {"code": "invalid_response", "message": str(payload)}}
    return payload, resp.status_code


async def bus_wait(
    *,
    thread: str,
    after_turn: int,
    wait_seconds: float,
    from_agent: str,
    completion: str = "first_reply_from",
) -> tuple[dict[str, Any], int]:
    """GET ``/threads/{thread}/wait`` for pre-consolidate idle polling."""
    params = {
        "after_turn": after_turn,
        "wait": min(wait_seconds, 60.0),
        "completion": completion,
        "from_agent": from_agent,
    }
    path = f"/threads/{thread}/wait"
    return await bus_get(path, params=params)


async def bus_fetch_turn(*, thread: str, turn_number: int) -> dict[str, Any] | None:
    """GET ``/turns/by-number`` for a single worker CLOSEOUT body."""
    payload, status = await bus_get(
        "/turns/by-number",
        params={"thread": thread, "turn_number": str(turn_number)},
    )
    if status >= 400 or not isinstance(payload, dict):
        return None
    return payload


__all__ = [
    "bus_fetch_turn",
    "bus_get",
    "bus_send",
    "bus_wait",
    "cdp_ask_harvest",
    "cortex_dispatch",
    "stargate_post",
    "step_output_json",
]
