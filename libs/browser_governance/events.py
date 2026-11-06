"""Browser governance observation events and best-effort ingest."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from typing import Any

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory


def _live_events_publish_disabled() -> bool:
    """Skip live Event Service writes during pytest unless explicitly opted in."""
    if os.environ.get("ULG_EVENTS_ENABLE") == "1":
        return False
    if os.environ.get("ULG_EVENTS_DISABLED") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


@event_factory
def browser_resource_census(
    *,
    host: str,
    mem_available_mib: float | None,
    swap_free_mib: float | None,
    swap_total_mib: float | None,
    disk_free_gib: float | None,
    inode_free_pct: float | None,
    total_pss_mib: float | None,
    total_rss_mib: float | None,
    cdp_ports_live: int,
    cdp_page_targets: int,
    profile_count: int,
    details: dict[str, Any] | None = None,
) -> Event:
    """Node-level browser resource census snapshot."""
    payload: dict[str, Any] = {
        "host": host,
        "mem_available_mib": mem_available_mib,
        "swap_free_mib": swap_free_mib,
        "swap_total_mib": swap_total_mib,
        "disk_free_gib": disk_free_gib,
        "inode_free_pct": inode_free_pct,
        "total_pss_mib": total_pss_mib,
        "total_rss_mib": total_rss_mib,
        "cdp_ports_live": cdp_ports_live,
        "cdp_page_targets": cdp_page_targets,
        "profile_count": profile_count,
    }
    if details is not None:
        payload["details"] = details
    return Event(
        signal="browser.resource.census",
        role="observation",
        scope="node",
        payload=payload,
    )


@event_factory
def browser_admission_warn(
    *,
    host: str,
    caller: str,
    warnings: list[str],
    mem_available_mib: float | None,
    swap_free_mib: float | None,
    disk_free_gib: float | None,
) -> Event:
    """Warn-only admission preflight observation."""
    return Event(
        signal="browser.admission.warn",
        role="observation",
        scope="node",
        payload={
            "host": host,
            "caller": caller,
            "warnings": warnings,
            "mem_available_mib": mem_available_mib,
            "swap_free_mib": swap_free_mib,
            "disk_free_gib": disk_free_gib,
        },
    )


@event_factory
def browser_supervision_error(
    *,
    surface: str,
    display: str | None,
    cdp_port: int | None,
    error_kind: str,
    summary: str | None,
    visibility_state: str,
    app_health: dict[str, Any] | None = None,
) -> Event:
    """Supervision detected a UI or health error on a browser surface."""
    payload: dict[str, Any] = {
        "surface": surface,
        "display": display,
        "cdp_port": cdp_port,
        "error_kind": error_kind,
        "summary": summary,
        "visibility_state": visibility_state,
    }
    if app_health is not None:
        payload["app_health"] = app_health
    return Event(
        signal="browser.supervision.error",
        role="observation",
        scope="node",
        payload=payload,
    )


@event_factory
def browser_supervision_visibility(
    *,
    surface: str,
    display: str | None,
    cdp_port: int | None,
    visibility_state: str,
    auth_state: str,
    detail: str | None = None,
) -> Event:
    """Supervision visibility/auth observation for a browser surface."""
    payload: dict[str, Any] = {
        "surface": surface,
        "display": display,
        "cdp_port": cdp_port,
        "visibility_state": visibility_state,
        "auth_state": auth_state,
    }
    if detail is not None:
        payload["detail"] = detail
    return Event(
        signal="browser.supervision.visibility",
        role="observation",
        scope="node",
        payload=payload,
    )


def _ndjson_line(event: Event, *, source: str) -> bytes:
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": source,
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    return (json.dumps(payload) + "\n").encode()


def emit_browser_event(event: Event, *, source: str = "browser-governance") -> bool:
    """Best-effort Event Service ingest — never raises."""
    if _live_events_publish_disabled():
        return False
    line = _ndjson_line(event, source=source)
    with contextlib.suppress(Exception):
        combined = os.environ.get("EVENTS_INGEST_TCP", "").strip()
        if combined and ":" in combined:
            host, _, port_s = combined.rpartition(":")
            if host and port_s.isdigit():
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(1.0)
                    sock.connect((host.strip(), int(port_s)))
                    sock.sendall(line)
                return True
        sock_path = os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(sock_path)
            sock.sendall(line)
        return True
    return False
