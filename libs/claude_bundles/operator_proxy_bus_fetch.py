"""Read-only agent-bus turn fetch for operator-proxy hop live-leg projection."""

from __future__ import annotations

import os
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

_FETCH_LAST = 200
_TIMEOUT_S = 15.0
_LINEAGE_TIMEOUT_S = 5.0


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def fetch_turns_for_lane(
    lane: str,
    *,
    last: int = _FETCH_LAST,
    compact: bool = True,
    mark_read: bool = False,
) -> list[dict[str, Any]]:
    """Fetch recent turns for *lane* via the agent-bus HTTP API.

    Raises ``RuntimeError`` with a short message when the bus is unreachable or
    returns a non-success status.
    """
    params = {
        "thread": str(lane),
        "last": last,
        "compact": "true" if compact else "false",
        "mark_read": "true" if mark_read else "false",
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = client.get("/turns", params=params, headers=_auth_headers())
        if resp.status_code != 200:
            raise RuntimeError(
                f"agent-bus fetch HTTP {resp.status_code} for lane {lane}"
            )
        payload = resp.json()
    turns = list(payload.get("turns") or [])
    turns.sort(key=lambda t: int(t.get("turn_number") or t.get("id") or 0))
    return turns


def fetch_thread_lineage(worker: str) -> dict[str, Any] | None:
    """GET ``/threads/<worker>/lineage``. Any error returns None.

    Tests replace this function. ``lineage_status_for_execution`` is the lookup
    the render passes as ``lineage_lookup``.
    """
    try:
        with make_sync_client(
            DEFAULT_AGENT_BUS_URL, timeout=_LINEAGE_TIMEOUT_S
        ) as client:
            resp = client.get(f"/threads/{worker}/lineage", headers=_auth_headers())
            if resp.status_code != 200:
                return None
            payload = resp.json()
    except Exception:  # noqa: BLE001 — unverified liveness, never raise into a render
        return None
    return payload if isinstance(payload, dict) else None


def lineage_status_for_execution(worker: str, execution_id: str) -> str | None:
    """Return the dispatch link's ``terminal_status``, or None when unverified."""
    try:
        payload = fetch_thread_lineage(worker)
    except Exception:  # noqa: BLE001 — a replaced transport may raise
        return None
    if not isinstance(payload, dict):
        return None
    links = payload.get("dispatch_links")
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict):
            continue
        if str(link.get("execution_id")) != str(execution_id):
            continue
        status = link.get("terminal_status")
        if status is None or status == "":
            return None
        return str(status)
    return None


__all__ = [
    "fetch_thread_lineage",
    "fetch_turns_for_lane",
    "lineage_status_for_execution",
]
