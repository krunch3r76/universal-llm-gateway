"""Read-only agent-bus turn fetch for operator-proxy hop live-leg projection."""

from __future__ import annotations

import os
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

_FETCH_LAST = 200
_TIMEOUT_S = 15.0


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


__all__ = ["fetch_turns_for_lane"]
