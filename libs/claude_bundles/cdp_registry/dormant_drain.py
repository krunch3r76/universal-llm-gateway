"""Park live retained / orphaned-alive CDP hosts as dormant seats.

Retention used to end only when Chrome died on its own: hygiene re-kept every
live ``retained`` row, and the orphan reaper deliberately skips operator-proxy
rows. Hosts therefore accumulated until the X server ran out of clients. This
pass ends retention explicitly — binding the CSE URL first when it is missing, so
parking a host never costs the session.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_bundles import cdp_lane, cdp_orphans
from claude_bundles import cdp_registry_store as _store
from claude_bundles.cse_idle_probe import (
    in_flight_from_state,
    probe_page_liveness_sync,
)

from .dormant import host_protection_reason, make_dormant
from .models import _ListenFn
from .registry_module import registry_package

_DRAINABLE_STATUSES = frozenset({"retained", "orphaned_alive"})
_CSE_MARKER = "/cowork/cse_"


@dataclass
class DrainResult:
    """Per-row outcomes of one drain pass."""

    dormant: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)
    protected: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Render counts and ids for logging or an MCP response."""
        return {
            "dormant": sorted(self.dormant),
            "released": sorted(self.released),
            "protected": dict(sorted(self.protected.items())),
            "counts": {
                "dormant": len(self.dormant),
                "released": len(self.released),
                "protected": len(self.protected),
            },
        }


def _probe_cse_url(port: int) -> str | None:
    """Return a CSE URL open on *port*, or None."""
    from .session_address import _default_probe_page_urls

    with contextlib.suppress(Exception):
        for url in _default_probe_page_urls(port):
            if _CSE_MARKER in str(url):
                return str(url)
    return None


def _ensure_chat_url(registration_id: str, row: dict[str, Any]) -> bool:
    """Bind a live CSE URL onto a row that lacks one; True when bound."""
    if str(row.get("chat_url") or "").strip():
        return True
    port = row.get("port")
    if not isinstance(port, int):
        return False
    url = _probe_cse_url(port)
    if url is None:
        return False
    from .session_address import bind_session_address

    return bind_session_address(registration_id, chat_url=url)


def _streaming_protection_reason(
    row: dict[str, Any], *, is_listening: _ListenFn
) -> str | None:
    """Protect a live CSE page until its streaming lease has ended.

    Retained rows can outlive the execution-store process that created them.
    Registry status alone therefore cannot prove that killing the host is safe;
    probe every attached CSE page and fail closed when liveness is unavailable.
    An idle probe returns ``None`` so the same host can be parked or reused
    immediately on the next lifecycle operation.
    """
    port = row.get("port")
    if not isinstance(port, int) or not is_listening(port):
        return None
    page_list = cdp_orphans._fetch_json(f"http://127.0.0.1:{port}/json/list")
    if page_list is None:
        return "stream_probe_unavailable"
    for page in cdp_orphans.cse_pages_from_list(page_list):
        websocket_url = page.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str) or not websocket_url.strip():
            return "stream_probe_unavailable"
        state, probe_ok = probe_page_liveness_sync(port, websocket_url)
        if not probe_ok or state is None:
            return "stream_probe_unavailable"
        if in_flight_from_state(state):
            return "streaming_monitoring"
    return None


def drain_live_hosts_to_dormant(
    *,
    is_listening: _ListenFn | None = None,
    release_unbound: bool = True,
    is_busy: Callable[[str], bool] | None = None,
) -> DrainResult:
    """Park every drainable host as dormant; release hosts holding no session.

    A host with no reachable CSE URL is a leaked Chrome rather than a seat, so
    with *release_unbound* it is killed instead of parked. *is_busy* lets the
    caller protect a host whose turn is still running.
    """
    listen = is_listening or cdp_lane.is_listening
    result = DrainResult()
    for registration_id, row in list(_store.load_active().items()):
        if row.get("status") not in _DRAINABLE_STATUSES:
            continue
        if is_busy is not None and is_busy(registration_id):
            result.protected[registration_id] = "paste_in_flight"
            continue
        protection = host_protection_reason(row, registration_id=registration_id)
        if protection is not None:
            result.protected[registration_id] = protection
            continue
        protection = _streaming_protection_reason(row, is_listening=listen)
        if protection is not None:
            result.protected[registration_id] = protection
            continue
        if _ensure_chat_url(registration_id, row):
            if make_dormant(registration_id, reason="hygiene_drain", is_listening=listen):
                result.dormant.append(registration_id)
            else:
                result.protected[registration_id] = "dormant_refused"
            continue
        if not release_unbound:
            result.protected[registration_id] = "no_chat_url"
            continue
        _release_unbound_host(registration_id, row, listen=listen)
        result.released.append(registration_id)
    if result.dormant or result.released:
        _store.append_log("dormant_drain", result.as_dict())
    return result


def _release_unbound_host(
    registration_id: str, row: dict[str, Any], *, listen: _ListenFn
) -> None:
    """Kill and release a host that holds no recoverable CSE session."""
    registry = registry_package()
    port = row.get("port")
    if isinstance(port, int) and listen(port):
        with contextlib.suppress(Exception):
            registry._kill_listener(port)
    with contextlib.suppress(Exception):
        registry.deregister_lane(registration_id, kill=True, reason="released")
