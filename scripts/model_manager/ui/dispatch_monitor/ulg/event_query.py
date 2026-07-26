"""Synchronous Event Service named-op client over the query UDS."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

_DEFAULT_QUERY_SOCK = "/tmp/universal-protocol/events-query.sock"


def query_sock() -> str:
    return os.environ.get("EVENTS_QUERY_SOCK", _DEFAULT_QUERY_SOCK)


def post_query(body: dict[str, Any], *, sock: str | None = None) -> dict[str, Any]:
    """POST ``body`` to ``/v1/query`` and return the JSON response."""
    path = sock or query_sock()
    transport = httpx.HTTPTransport(uds=path)
    with httpx.Client(transport=transport, timeout=30.0) as client:
        resp = client.post("http://localhost/v1/query", json=body)
        resp.raise_for_status()
        return resp.json()


def _operation(name: str, params: dict[str, Any], *, sock: str | None = None) -> dict[str, Any]:
    return post_query({"type": "operation", "name": name, "params": params}, sock=sock)


def signal_events(
    signal: str,
    *,
    minutes: int = 60,
    limit: int = 500,
    sock: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent persisted events matching ``signal`` (glob supported)."""
    result = _operation(
        "signal-events",
        {"signal": signal, "minutes": minutes, "limit": limit},
        sock=sock,
    )
    rows = result.get("rows")
    return list(rows) if isinstance(rows, list) else []


def charter_tick_audit(*, minutes: int = 60, limit: int = 200, sock: str | None = None) -> dict[str, Any]:
    """Run the ``manage.charter.tick.audit`` named op."""
    return _operation(
        "manage.charter.tick.audit",
        {"minutes": minutes, "limit": limit},
        sock=sock,
    )
