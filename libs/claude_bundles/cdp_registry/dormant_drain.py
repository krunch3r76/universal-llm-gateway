"""Park live retained / orphaned-alive / active CDP hosts as dormant seats.

Retention used to end only when Chrome died on its own: hygiene re-kept every
live ``retained`` row, and the orphan reaper deliberately skips operator-proxy
rows. Hosts therefore accumulated until the X server ran out of clients. This
pass ends retention explicitly — binding the CSE URL first when it is missing, so
parking a host never costs the session.

Operator-proxy/mission rows sitting plain ``active`` (the ordinary in-progress
state, not merely ``retained``) previously had no path into this sweep at all —
a seat that never cycled through dormancy could hold its Chrome open
indefinitely. ``_idle_reachable_protects`` no longer shields identified
operator-proxy / mission hosts when idle; only missing or blank purpose still
fail-closes to protect.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_bundles import cdp_lane, cdp_orphans
from claude_bundles import cdp_registry_store as _store
from claude_bundles.cse_idle_probe import (
    in_flight_from_state,
    probe_page_liveness_sync,
)
from claude_bundles.operator_proxy_mission import is_operator_proxy_mission_purpose

from .dormant import host_protection_reason, make_dormant
from .driver_locks import process_holds_driver_lock
from .models import _ListenFn
from .registry_module import registry_package

# Shadows package CONSUMERS: GIW imports the package, not this drain module.
CONSUMERS: tuple[str, ...] = ("cdp_ask",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

_DRAINABLE_STATUSES = frozenset({"retained", "orphaned_alive", "active"})
_CSE_MARKER = "/cowork/cse_"

# Long relative to one dispatch (observed 5-25 min the night this shipped):
# the only signal available on a registry row is a lifecycle-transition
# timestamp, not a per-turn heartbeat, so the window must tolerate a hot seat
# that simply has not cycled through dormancy recently.
_DEFAULT_OPERATOR_IDLE_GRACE_S = 1800.0


def operator_idle_grace_s() -> float:
    """Idle grace window (``CDP_OPERATOR_IDLE_GRACE_S``) before an
    operator-proxy/mission seat becomes drainable."""
    raw = os.environ.get("CDP_OPERATOR_IDLE_GRACE_S", "").strip()
    if raw:
        with contextlib.suppress(ValueError):
            value = float(raw)
            if value > 0:
                return value
    return _DEFAULT_OPERATOR_IDLE_GRACE_S


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


def _idle_reachable_protects(row: dict[str, Any], *, now: float | None = None) -> bool:
    """True when an idle reachable CSE page must keep its Chrome.

    Purpose missing or blank still fail-closes to protect — absence of that
    signal is the class that produced this arc. Identified operator-proxy /
    mission hosts drain when idle (no grace window). One-shot ``ask`` hosts
    stay drainable when idle so leaked Chromes still park. *now* is retained
    for call-site compatibility; idle protection no longer depends on age.
    """
    _ = now
    purpose = row.get("purpose")
    if purpose is None or not str(purpose).strip():
        return True
    if is_operator_proxy_mission_purpose(str(purpose)):
        return False
    return False


def row_drain_protection(
    row: dict[str, Any],
    *,
    registration_id: str,
    is_listening: _ListenFn | None = None,
    is_busy: Callable[[str], bool] | None = None,
    now: float | None = None,
) -> str | None:
    """Return why hygiene must skip this row, or None when it may park/release.

    Public probe surface — does not mutate the registry. Same predicate
    ``drain_live_hosts_to_dormant`` applies before ``make_dormant``.

    ``execution_in_flight`` is read from the row's durable ``execution_state``
    before any probe or lock: it is the only protection that survives the
    process that admitted the execution, so the hygiene pass of a freshly
    recycled cdp_ask cannot park a host the previous process was still
    driving (a:36948). ``process_driver_lock`` is a driver lock held by this
    process. That hold is invisible to ``host_protection_reason``
    (``driver_attached`` is only a flock held by another process), so it is
    decided here before park or release.
    """
    from .execution_state import row_execution_in_flight

    if row_execution_in_flight(row, now=now) is not None:
        return "execution_in_flight"
    if process_holds_driver_lock(registration_id):
        return "process_driver_lock"
    listen = is_listening or cdp_lane.is_listening
    if is_busy is not None and is_busy(registration_id):
        return "paste_in_flight"
    protection = host_protection_reason(row, registration_id=registration_id)
    if protection is not None:
        return protection
    return _streaming_protection_reason(row, is_listening=listen, now=now)


def _streaming_protection_reason(
    row: dict[str, Any], *, is_listening: _ListenFn, now: float | None = None
) -> str | None:
    """Protect a reachable CSE page that is still a seat, not a leak.

    Retained (and now plain-active) rows can outlive the execution-store
    process that created them. Registry status alone therefore cannot prove
    that killing the host is safe; probe every attached CSE page and fail
    closed when liveness is unavailable.

    A successful idle probe is not a drain warrant for blank-purpose seats
    (fail-closed protect); identified operator-proxy / mission hosts drain
    when idle — see ``_idle_reachable_protects``. One-shot ``ask`` hosts still
    return ``None`` when idle so hygiene can park them.

    Probe-source failure is not an empty page list. A wedged, silent, or
    unparseable CDP port must protect — with a distinct reason so a leaked
    host stays greppable. A successful list with no CSE page stays drainable.
    """
    port = row.get("port")
    if not isinstance(port, int) or not is_listening(port):
        return "cdp_port_unreachable"
    page_list = cdp_orphans._fetch_json(f"http://127.0.0.1:{port}/json/list")
    if page_list is None:
        return "stream_probe_unavailable"
    if not isinstance(page_list, list):
        return "cdp_list_unparseable"
    for page in cdp_orphans.cse_pages_from_list(page_list):
        websocket_url = page.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str) or not websocket_url.strip():
            return "stream_probe_unavailable"
        state, probe_ok = probe_page_liveness_sync(port, websocket_url)
        if not probe_ok or state is None:
            return "stream_probe_unavailable"
        if in_flight_from_state(state):
            return "streaming_monitoring"
        if _idle_reachable_protects(row, now=now):
            return "reachable_operator_seat"
    return None


def drain_live_hosts_to_dormant(
    *,
    display: str | None = None,
    is_listening: _ListenFn | None = None,
    release_unbound: bool = True,
    is_busy: Callable[[str], bool] | None = None,
    now: float | None = None,
) -> DrainResult:
    """Park every drainable host as dormant; release hosts holding no session.

    A host with no reachable CSE URL is a leaked Chrome rather than a seat, so
    with *release_unbound* it is killed instead of parked. *is_busy* lets the
    caller protect a host whose turn is still running. *now* overrides the
    clock used for the operator-proxy idle-grace check (tests only; omit in
    production so it reads the real clock).

    When *display* is set, only rows whose recorded ``display`` matches are
    considered; omit *display* to drain across all displays (legacy behavior).
    """
    listen = is_listening or cdp_lane.is_listening
    display_filter = str(display).strip() if display is not None else None
    result = DrainResult()
    for registration_id, row in list(_store.load_active().items()):
        if display_filter is not None:
            row_display = str(row.get("display") or "").strip()
            if row_display != display_filter:
                continue
        if row.get("status") not in _DRAINABLE_STATUSES:
            continue
        protection = row_drain_protection(
            row,
            registration_id=registration_id,
            is_listening=listen,
            is_busy=is_busy,
            now=now,
        )
        if protection is not None:
            result.protected[registration_id] = protection
            continue
        if _ensure_chat_url(registration_id, row):
            if make_dormant(
                registration_id, reason="hygiene_drain", is_listening=listen
            ):
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
