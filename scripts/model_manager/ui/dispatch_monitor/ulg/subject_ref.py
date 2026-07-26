"""Resolve operator subject refs to a thread id for scoped reconcile."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from transport_utils import AGENT_BUS_SOCK

_DEFAULT_TIMEOUT_S = 3.0


def _bus_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _bus_get(
    path: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
    get_json: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    if get_json is not None:
        try:
            body = get_json(path)
            return body if isinstance(body, dict) else None
        except Exception:
            return None
    transport = httpx.HTTPTransport(uds=AGENT_BUS_SOCK)
    try:
        with httpx.Client(
            transport=transport,
            timeout=timeout,
            headers=_bus_headers(),
        ) as client:
            response = client.get(f"http://localhost{path}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            body = response.json()
            return body if isinstance(body, dict) else None
    except Exception:
        return None


def resolve_thread_id(
    subject: str,
    *,
    bus_get: Callable[[str], dict[str, Any] | None] | None = None,
) -> str | None:
    """Map ``dispatch_id`` / ``root_id`` / ``request_id`` to a worker thread id."""
    ref = subject.strip()
    if not ref:
        return None
    if ref.isdigit():
        return ref
    getter = bus_get or (lambda path: _bus_get(path))
    link = getter(f"/dispatch-links/{ref}")
    if isinstance(link, dict) and link.get("thread_id"):
        return str(link["thread_id"])
    thread = getter(f"/threads/{ref}")
    if isinstance(thread, dict) and thread.get("id"):
        return str(thread["id"])
    return ref
