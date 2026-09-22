"""HTTP helpers for operator-hop-harvest handlers."""

from __future__ import annotations

import os
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

_HTTP_TIMEOUT_S = 30.0


def step_output_json(outputs: dict[str, Any], step_name: str) -> dict[str, Any]:
    raw = outputs.get(step_name)
    if raw is None:
        return {}
    if hasattr(raw, "json"):
        data = getattr(raw, "json", None)
        return data if isinstance(data, dict) else {}
    if isinstance(raw, dict):
        inner = raw.get("json")
        return inner if isinstance(inner, dict) else raw
    return {}


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


async def bus_get(
    path: str, *, params: dict[str, Any] | None = None
) -> tuple[dict[str, Any], int]:
    async with make_async_client(
        DEFAULT_AGENT_BUS_URL, timeout=_HTTP_TIMEOUT_S
    ) as client:
        try:
            resp = await client.get(path, params=params or {}, headers=_bus_headers())
        except Exception as exc:  # noqa: BLE001
            return {
                "error": {"code": "agent_bus_unreachable", "message": str(exc)}
            }, 503
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {
            "error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}
        }
    if not isinstance(payload, dict):
        payload = {"error": {"code": "invalid_response", "message": str(payload)}}
    return payload, resp.status_code


async def bus_fetch_turn(*, thread: str, turn_number: int) -> dict[str, Any] | None:
    payload, status = await bus_get(
        "/turns/by-number",
        params={"thread": thread, "turn_number": str(turn_number)},
    )
    if status >= 400 or not isinstance(payload, dict):
        return None
    return payload.get("turn") if isinstance(payload.get("turn"), dict) else payload
