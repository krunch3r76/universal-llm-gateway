"""Bounded one-shot fetches for click-time reconcile (G5.1 slice 2)."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from transport_utils import AGENT_BUS_SOCK, CORTEX_API_SOCK

from scripts.model_manager.ui.dispatch_monitor.ulg.lease_snapshot import worker_url

_BUS_TIMEOUT_S = 3.0
_LEDGER_TIMEOUT_S = 3.0
_CORTEX_TIMEOUT_S = 3.0

_DISPATCH_STATUS_PATH = "/api/v1/git/admin/dispatch-status"
_SCOREBOARD_URI_RE = re.compile(r"(cortex://\S*scoreboard\S*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    source: str
    ok: bool
    data: dict[str, Any] | None
    error: str | None = None


def _bus_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _http_get_json(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    uds: str | None = None,
    get_json: Callable[[str], dict[str, Any] | None] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if get_json is not None:
        try:
            body = get_json(url)
            if body is None:
                return None, "empty_response"
            return (body, None) if isinstance(body, dict) else (None, "malformed_json")
        except Exception as exc:  # noqa: BLE001 — reconcile must not raise
            return None, str(exc)
    try:
        if uds:
            transport = httpx.HTTPTransport(uds=uds)
            client_kwargs: dict[str, Any] = {"transport": transport, "timeout": timeout}
        else:
            client_kwargs = {"timeout": timeout}
        with httpx.Client(**client_kwargs) as client:
            response = client.get(url, headers=headers or {})
            if response.status_code == 404:
                return None, "not_found"
            response.raise_for_status()
            body = response.json()
            return (body, None) if isinstance(body, dict) else (None, "malformed_json")
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def fetch_bus_thread(
    thread_id: str,
    *,
    get_json: Callable[[str], dict[str, Any] | None] | None = None,
) -> SourceOutcome:
    """Read agent-bus thread detail for one subject."""
    path = f"/threads/{thread_id}"
    if get_json is not None:
        body, err = _http_get_json(path, timeout=_BUS_TIMEOUT_S, get_json=get_json)
    else:
        url = f"http://localhost{path}"
        body, err = _http_get_json(
            url,
            timeout=_BUS_TIMEOUT_S,
            headers=_bus_headers(),
            uds=AGENT_BUS_SOCK,
        )
    if err:
        return SourceOutcome("bus", False, None, err)
    return SourceOutcome("bus", True, body)


def fetch_ledger_dispatch(
    thread_id: str,
    *,
    base_url: str | None = None,
    get_json: Callable[[str], dict[str, Any] | None] | None = None,
) -> SourceOutcome:
    """Read git-worker dispatch-status for one thread."""
    url = f"{worker_url(base_url)}{_DISPATCH_STATUS_PATH}?thread_id={thread_id}"
    body, err = _http_get_json(url, timeout=_LEDGER_TIMEOUT_S, get_json=get_json)
    if err:
        return SourceOutcome("ledger", False, None, err)
    return SourceOutcome("ledger", True, body)


def _scoreboard_uri_for_thread(thread_id: str, bus: dict[str, Any] | None) -> str:
    if isinstance(bus, dict):
        for key in ("scoreboard_uri", "scoreboard"):
            value = bus.get(key)
            if isinstance(value, str) and value.startswith("cortex://"):
                return value
        summary = bus.get("summary")
        if isinstance(summary, str):
            match = _SCOREBOARD_URI_RE.search(summary)
            if match:
                return match.group(1).rstrip(".,)")
    return f"cortex://notes/system/threads/{thread_id}-charter-scoreboard.md"


def fetch_cortex_scoreboard(
    thread_id: str,
    *,
    bus_data: dict[str, Any] | None = None,
    get_json: Callable[[str], dict[str, Any] | None] | None = None,
) -> SourceOutcome:
    """Read cortex scoreboard markdown for one thread (conventional URI)."""
    uri = _scoreboard_uri_for_thread(thread_id, bus_data)
    rel = uri.removeprefix("cortex://")
    path = f"/v1/read?path={rel}"
    if get_json is not None:
        body, err = _http_get_json(path, timeout=_CORTEX_TIMEOUT_S, get_json=get_json)
    else:
        url = f"http://localhost{path}"
        body, err = _http_get_json(url, timeout=_CORTEX_TIMEOUT_S, uds=CORTEX_API_SOCK)
    if err:
        return SourceOutcome("cortex", False, {"uri": uri}, err)
    content = body.get("content") if isinstance(body, dict) else None
    if not isinstance(content, str):
        return SourceOutcome("cortex", False, {"uri": uri}, "missing_content")
    return SourceOutcome(
        "cortex",
        True,
        {"uri": uri, "content": content, "line_count": content.count("\n") + 1},
    )
