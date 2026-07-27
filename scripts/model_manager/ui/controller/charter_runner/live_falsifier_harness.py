"""HTTP cortex helpers for live falsifier probes (dispatch sandbox lacks local SQLite)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from transport_utils import CORTEX_API_SOCK


def _cortex_client() -> httpx.Client:
    uds = os.environ.get("CORTEX_API_SOCK", CORTEX_API_SOCK)
    transport = httpx.HTTPTransport(uds=uds)
    return httpx.Client(transport=transport, base_url="http://cortex", timeout=15.0)


def cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    with _cortex_client() as client:
        resp = client.post(
            "/dispatch",
            json={"tool": tool, "arguments": arguments},
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and "error" in payload:
            raise RuntimeError(str(payload["error"]))
        return payload


def live_density_triage_lookup(todo_ref: str) -> str | None:
    """Live cortex entity_get — same field ``default_density_triage_lookup`` reads."""
    ent = cortex_dispatch(
        "entity_get", {"entity_id": todo_ref, "intent": "full"}
    )
    attrs = ent.get("attributes") or {}
    if not isinstance(attrs, dict):
        return None
    raw = attrs.get("density_triage")
    return str(raw).strip() if raw is not None else None


def live_assertion_get(assertion_id: int | str) -> dict[str, Any]:
    return cortex_dispatch(
        "assertion_get", {"assertion_id": int(assertion_id), "intent": "full"}
    )
